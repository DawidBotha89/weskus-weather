"""NOAA mirror outages must recover without changing dates, units or coverage."""
from copy import deepcopy
from datetime import UTC, datetime
from urllib.error import HTTPError
from unittest import TestCase
from unittest.mock import patch

import marine

NOW = datetime(2026, 11, 18, 18, tzinfo=UTC)
PRIMARY, AWS, GOOGLE = marine.OISST_SOURCES


def missing(url):
    return HTTPError(url, 404, "Not found", {}, None)


def rows(day="2026-11-17", url="source"):
    return {area: {"valid_utc": day + "T12:00:00Z", "temperature_c": 16,
                   "latitude": spec[2][0], "longitude": spec[2][1], "source_url": url}
            for area, spec in marine.AREAS.items()}


class SstRecoveryTests(TestCase):
    def test_mirror_new_day_precedes_older_primary_and_preserves_etag_guard(self):
        def fetch(url, **kwargs):
            if url.startswith(PRIMARY) and "20261117" in url:
                raise missing(url)
            return (b"data", {"ETag": "cloud-revision"}) if kwargs["method"] == "HEAD" else (b"payload", {})
        # GET has no explicit method argument; record it separately.
        def request(url, **kwargs):
            return fetch(url, **{"method": "GET", **kwargs})
        with patch.object(marine, "fetch", side_effect=request) as call, \
                patch.object(marine, "sample_sst", side_effect=lambda b,d,u: rows(str(d),u)):
            token, result = marine.read_sst(NOW, None, False)
        self.assertTrue(token["source_url"].startswith(AWS))
        self.assertTrue(all(r["valid_utc"] == "2026-11-17T12:00:00Z" for r in result.values()))
        self.assertFalse(any("20261116" in c.args[0] for c in call.call_args_list))
        self.assertEqual(call.call_args.kwargs["headers"], {"If-Match": "cloud-revision"})

    def test_download_failure_uses_second_cloud_and_cache_cannot_cross_hosts(self):
        def request(url, **kwargs):
            if url.startswith(PRIMARY):
                raise TimeoutError("primary unavailable")
            if url.startswith(AWS) and kwargs.get("method") != "HEAD":
                raise HTTPError(url, 503, "Unavailable", {}, None)
            return b"payload", {"ETag": "same-etag"}
        cache = {"source_url": PRIMARY + "/202611/oisst-avhrr-v02r01.20261117_preliminary.nc",
                 "etag": "same-etag", "last_modified": None}
        for complete in (True, False):
            with self.subTest(complete=complete), patch.object(marine, "fetch", side_effect=request) as call, \
                    patch.object(marine, "sample_sst", return_value=rows()) as sample:
                token, result = marine.read_sst(NOW, cache, complete)
            self.assertTrue(token["source_url"].startswith(GOOGLE))
            sample.assert_called_once()
            self.assertEqual(len([c for c in call.call_args_list if c.args[0].startswith(PRIMARY)]), 1)
            self.assertEqual(set(result), set(marine.AREAS))

    def test_recovered_mirror_cache_reuses_original_readings(self):
        url = AWS + "/202611/oisst-avhrr-v02r01.20261117_preliminary.nc"
        cache = {"source_url": url, "etag": "rev", "last_modified": None}
        def request(url, **kwargs):
            if url.startswith(PRIMARY):
                raise missing(url)
            self.assertEqual(kwargs["method"], "HEAD")
            return b"", {"ETag": "rev"}
        with patch.object(marine, "fetch", side_effect=request), patch.object(marine, "sample_sst") as sample:
            self.assertEqual(marine.read_sst(NOW, cache, True), (cache, None))
            sample.assert_not_called()

    def test_invalid_or_incomplete_payload_falls_back_without_accepting_bad_cells(self):
        for invalid in (ValueError("Wrong units/date/grid"), {"columbine": {}}):
            with self.subTest(invalid=invalid), \
                    patch.object(marine, "fetch", return_value=(b"payload", {"ETag": "rev"})), \
                    patch.object(marine, "sample_sst", side_effect=[invalid, rows()]):
                token, result = marine.read_sst(NOW, None, False)
                self.assertTrue(token["source_url"].startswith(AWS))
                self.assertEqual(set(result), set(marine.AREAS))

    def test_unavailable_hosts_are_bounded_to_one_attempt_each(self):
        with patch.object(marine, "fetch", side_effect=TimeoutError("offline")) as call:
            with self.assertRaisesRegex(RuntimeError, "NOAA mirrors"):
                marine.read_sst(NOW, None, False)
        self.assertEqual(call.call_count, 3)

    def test_missing_days_never_extend_freshness_window(self):
        def request(url, **kwargs):
            raise missing(url)
        with patch.object(marine, "fetch", side_effect=request) as call:
            with self.assertRaises(RuntimeError):
                marine.read_sst(NOW, None, False)
        self.assertEqual(call.call_count, 5 * 3 * 2)
        self.assertFalse(any("20261112" in c.args[0] for c in call.call_args_list))

    def test_all_sources_down_retains_original_sst_dates_and_surfaces_failure(self):
        previous = {"towns": {"langebaan": {"area_name": marine.AREAS["saldanha"][0],
                       "sst": rows("2026-11-16")["saldanha"]}}}
        saved = deepcopy(previous)
        with patch.object(marine, "fetch", side_effect=TimeoutError("offline")), \
                patch.object(marine, "read_waves", return_value=({}, None)):
            result = marine.update_marine(previous, now=NOW)
        self.assertEqual(previous, saved)
        self.assertEqual(result["towns"]["langebaan"]["sst"], saved["towns"]["langebaan"]["sst"])
        self.assertEqual(result["refresh_errors"], ["sst"])
