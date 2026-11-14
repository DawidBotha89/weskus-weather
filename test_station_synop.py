"""FM-12 station coverage, units, correction precedence and failure recovery."""
from datetime import UTC, datetime
import unittest
from unittest.mock import patch

import station_synop as synop
import stations

NOW = datetime(2026, 11, 14, 19, tzinfo=UTC)
# Real section-1 report groups retrieved from SAWS SMZA02 FAPR 141800.
REAL = b"SMZA02 FAPR 141800\r\r\nAAXX 14184\n68613 36/// /2007 10181 20110 30034 40145 53001 333 10239 20181 91013 555 91014=\n68715 36/// /1403 10238 20110 30032 48576 52011 333 10335 20238 91006 555 91015="


class SynopTests(unittest.TestCase):
    def test_real_reports_knots_temperature_not_section_three_maximum(self):
        a, b = synop.parse_bulletin(REAL, NOW)
        self.assertEqual((a["id"], a["temperature_c"], a["dewpoint_c"]), ("68613", 18.1, 11))
        self.assertAlmostEqual(a["wind_speed_ms"], 3.6011)
        self.assertEqual((a["wind_from_deg"], b["temperature_c"], b["wind_from_deg"]), (200, 23.8, 140))
        self.assertEqual(a["observed_utc"], "2026-11-14T18:00:00Z")

    def test_missing_fields_calm_variable_and_high_wind(self):
        data = b"SMZA02 FAPR 141800\nAAXX 14181\n68613 36/// /0000 1//// 29080=68715 36/// /9999 00101 11005 21105="
        calm, fast = synop.parse_bulletin(data, NOW)
        self.assertIsNone(calm["temperature_c"])
        self.assertEqual(calm["reported_rh"], 80)
        self.assertEqual(calm["wind_speed_ms"], 0)
        self.assertIsNone(calm["wind_from_deg"])
        self.assertEqual((fast["temperature_c"], fast["dewpoint_c"], fast["wind_speed_ms"]), (-.5, -10.5, 101))
        self.assertIsNone(fast["wind_from_deg"])

    def test_month_boundary_and_timestamp_disagreement(self):
        reference = datetime(2027, 1, 1, 0, 40, tzinfo=UTC)
        self.assertEqual(synop.report_time(31, 18, reference), datetime(2026, 12, 31, 18, tzinfo=UTC))
        for data in (REAL.replace(b"AAXX 14184", b"AAXX 14124"), REAL.replace(b"FAPR 141800", b"FAPR 151800")):
            with self.assertRaises(ValueError):
                synop.parse_bulletin(data, NOW)

    def test_correction_beats_late_routine_and_unknown_stations_ignored(self):
        corrected = synop.parse_bulletin(REAL.replace(b"141800", b"141800 CCA"), NOW)[0]
        corrected["available_utc"] = "2026-11-14T18:40:00Z"
        routine = {"available_utc": "2026-11-14T18:50:00Z"}
        self.assertGreater(synop.preference(corrected), synop.preference(routine))
        self.assertEqual(synop.parse_bulletin(REAL.replace(b"68613", b"99999").replace(b"68715", b"99998"), NOW), [])

    def test_bridge_fallback_cache_and_authoritative_coordinates(self):
        item = ("public-key", "revision", "2026-11-14T18:39:06Z")
        def index(bridge, now):
            if bridge == synop.BRIDGES[0]:
                raise OSError("temporary access error")
            return [item]
        with patch.object(synop, "list_bulletins", side_effect=index), patch.object(synop, "fetch", return_value=(REAL, {})):
            rows, cache, errors = synop.collect_bulletins(None, NOW, stations.STATIONS, stations.towns(), stations.humidity)
        self.assertEqual(errors, [])
        row = dict(rows)["lambertsbaai"]
        self.assertEqual((row["latitude"], row["longitude"]), (-32.0351, 18.3326))
        self.assertLess(row["distance_km"], 10)
        self.assertIn("oscar.wmo.int", row["coordinate_metadata_url"])
        with patch.object(synop, "list_bulletins", return_value=[item]), patch.object(synop, "fetch", side_effect=AssertionError("redownload")):
            self.assertEqual(synop.collect_bulletins({"bulletin_cache": cache, "towns": dict(rows)}, NOW, stations.STATIONS, stations.towns(), stations.humidity), ([], cache, []))

    def test_partial_reports_survive_both_bridge_failures_and_empty_index_errors(self):
        item = ("public-good", "revision", "2026-11-14T18:39:06Z")
        bad = ("public-bad", "revision", "2026-11-14T18:40:06Z")
        def read(url, **kwargs):
            if url.endswith("public-bad"):
                raise OSError("access error")
            return REAL, {}
        with patch.object(synop, "list_bulletins", return_value=[item, bad]), patch.object(synop, "fetch", side_effect=read):
            rows, cache, errors = synop.collect_bulletins(None, NOW, stations.STATIONS, stations.towns(), stations.humidity)
        self.assertEqual(set(dict(rows)), {"malmesbury", "lambertsbaai"})
        self.assertTrue(errors)
        self.assertEqual(cache, {synop.CACHE+item[0]: "revision"})
        with patch.object(synop, "list_bulletins", return_value=[]):
            self.assertTrue(synop.collect_bulletins(None, NOW, stations.STATIONS, stations.towns(), stations.humidity)[2])

    def test_history_and_provenance_survive_failure_and_unavailable_towns_explicit(self):
        prior = {"station_id": stations.STATIONS["malmesbury"][0], "observed_utc": "2026-11-12T18:00:00Z", "source_url": "original-public-url"}
        with (patch.object(stations, "latest_reports", return_value=[]),
              patch.object(stations, "collect_bulletins", return_value=([], {}, ["unavailable"]))):
            result = stations.update_stations({"towns": {"malmesbury": prior}}, now=NOW)
        self.assertEqual(result["towns"]["malmesbury"], prior)
        self.assertEqual(result["history"]["malmesbury"], [prior])
        self.assertEqual(result["refresh_errors"], ["synop_bulletins"])
        self.assertEqual(len(result["coverage"]), 15)
        self.assertEqual(result["coverage"]["piketberg"]["status"], "unavailable")

    def test_older_fallback_merges_newer_primary_and_keeps_unrecovered_warning(self):
        good = ("dwd/A_SMZA02FAPR141800_C_EDZW_ok", "1", "2026-11-14T18:39:06Z")
        failed = ("dwd/A_SMZA02FAPR141800CCA_C_EDZW_bad", "2", "2026-11-14T18:40:06Z")
        older = ("jma/A_SMZA02FAPR141200_C_RJTD_old", "3", "2026-11-14T12:40:06Z")
        older_data = REAL.replace(b"141800", b"141200").replace(b"14184", b"14124")
        def read(url, **kwargs):
            if url.endswith("_bad"):
                raise OSError("access error")
            return (older_data if url.endswith("_old") else REAL), {}
        with (patch.object(synop, "list_bulletins", side_effect=[[good, failed], [older]]),
              patch.object(synop, "fetch", side_effect=read)):
            rows, cache, errors = synop.collect_bulletins(None, NOW, stations.STATIONS, stations.towns(), stations.humidity)
        self.assertEqual({r["observed_utc"] for _, r in rows}, {"2026-11-14T12:00:00Z", "2026-11-14T18:00:00Z"})
        self.assertEqual(set(cache), {synop.CACHE+good[0], synop.CACHE+older[0]})
        self.assertEqual(len(errors), 1)
        self.assertIn("_bad", errors[0])

        corrected = ("jma/A_SMZA02FAPR141800CCA_C_RJTD_corrected", "4", "2026-11-14T18:42:06Z")
        def recovered(url, **kwargs):
            if url.endswith("_bad"):
                raise OSError("access error")
            return (REAL.replace(b"141800", b"141800 CCA") if url.endswith("_corrected") else REAL), {}
        with (patch.object(synop, "list_bulletins", side_effect=[[good, failed], [corrected]]),
              patch.object(synop, "fetch", side_effect=recovered)):
            rows, cache, errors = synop.collect_bulletins(None, NOW, stations.STATIONS, stations.towns(), stations.humidity)
        self.assertEqual(errors, [])
        self.assertEqual(set(cache), {synop.CACHE+good[0], synop.CACHE+corrected[0]})
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()
