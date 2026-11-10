"""Offline checks for public station and offshore provenance, missing data and source reuse."""

from contextlib import redirect_stderr
from datetime import UTC, datetime
import io
import unittest
from unittest.mock import patch

import h5py
import numpy as np

import marine
import stations
from point_sources import stamp


NOW = datetime(2026, 11, 10, 19, tzinfo=UTC)


class StationTests(unittest.TestCase):
    def test_humidity_uses_measured_value_then_dewpoint(self):
        self.assertEqual(stations.humidity(80, None, None)[0], 80)
        self.assertEqual(stations.humidity(None, 287.15, 287.15)[0], 100)
        self.assertTrue(70 < stations.humidity(None, 293.15, 288.15)[0] < 75)
        self.assertEqual(stations.humidity(None, 285, None), (None, None))
        self.assertEqual(stations.humidity(None, 285, 297), (None, None))

    def test_station_cache_uses_matching_revision_and_preserves_measurement_time(self):
        slug = "paternoster"
        station = stations.STATIONS[slug][0]
        prior = {"station_id": station, "source_etag": "rev1", "observed_utc": stamp(NOW)}
        item = (NOW, "unused", "rev1", stamp(NOW))
        with (patch.dict(stations.STATIONS, {slug: stations.STATIONS[slug]}, clear=True),
              patch.object(stations, "latest_reports", return_value=[item]),
              patch.object(stations, "fetch", side_effect=AssertionError("cached payload downloaded"))):
            result = stations.update_stations({"towns": {slug: prior}}, now=NOW)
        self.assertEqual(result["towns"][slug], prior)
        self.assertEqual(result["history"][slug], [prior])
        self.assertEqual(result["refresh_errors"], [])
        self.assertNotIn("darling", result["towns"])

    def test_failure_at_one_station_does_not_update_times_or_drop_other_reports(self):
        old = {"towns": {"paternoster": {"station_id": stations.STATIONS["paternoster"][0],
                                         "observed_utc": "2026-11-09T12:00:00Z"}}}
        def list_reports(station, now):
            if station == stations.STATIONS["paternoster"][0]:
                raise OSError("source offline")
            return []
        with patch.object(stations, "latest_reports", side_effect=list_reports), redirect_stderr(io.StringIO()):
            result = stations.update_stations(old, now=NOW)
        self.assertEqual(result["towns"], old["towns"])
        self.assertEqual(result["refresh_errors"], ["paternoster"])
        self.assertFalse(result["history"])


class OffshoreTests(unittest.TestCase):
    def test_swell_index_selects_only_partition_one(self):
        idx = b"1:0:d=t:HTSGW:surface:\n2:10:d=t:SWELL:1 in sequence:\n3:20:d=t:SWELL:2 in sequence:\n4:40:d=t:SWPER:1 in sequence:\n5:50:d=t:SWPER:2 in sequence:\n"
        with patch.object(marine, "fetch", return_value=(idx, {})):
            _, fields, _ = marine.wave_index(NOW, 3)
        self.assertEqual(fields, {"SWELL": (10, 19), "SWPER": (40, 49)})

    def test_current_wave_and_sst_versions_only_probe_metadata(self):
        with (patch.object(marine, "wave_index", return_value=("wave-url", {}, "digest")),
              patch.object(marine, "fetch", return_value=(b"", {"ETag": "revision"})) as wave):
            token = {"source_url": "wave-url", "index_sha256": "digest", "etag": "revision"}
            self.assertEqual(marine.read_waves(NOW, token, True), (token, None))
        wave.assert_called_once_with("wave-url", limit=0, method="HEAD")
        url = marine.OISST + "/202611/oisst-avhrr-v02r01.20261109_preliminary.nc"
        with patch.object(marine, "fetch", return_value=(b"", {"ETag": "version"})) as sst:
            token = {"source_url": url, "etag": "version", "last_modified": None}
            self.assertEqual(marine.read_sst(NOW, token, True), (token, None))
        sst.assert_called_once_with(url, limit=0, method="HEAD")

    def test_daily_sst_uses_source_datum_time_units_and_wet_mask(self):
        data = io.BytesIO()
        with h5py.File(data, "w") as f:
            f.attrs["time_coverage_start"] = np.bytes_("2026-11-09T00:00:00Z")
            f.attrs["time_coverage_end"] = np.bytes_("2026-11-09T23:59:59Z")
            f["time"] = [17844.]
            f["time"].attrs["units"] = np.bytes_("days since 1978-01-01 12:00:00")
            f["lat"] = np.arange(720) * .25 - 89.875
            f["lon"] = np.arange(1440) * .25 + .125
            v = f.create_dataset("sst", (1, 1, 720, 1440), dtype="i2", fillvalue=-999, chunks=(1, 1, 1, 40))
            v.attrs.update({"units": np.bytes_("Celsius"), "_FillValue": [-999],
                            "scale_factor": [.01], "add_offset": [0]})
            lat, lon = marine.AREAS["columbine"][2]
            v[0, 0, round((lat + 89.875) / .25), round((lon - .125) / .25)] = 1651
        rows = marine.sample_sst(data.getvalue(), datetime(2026, 11, 9).date(), "test-source")
        self.assertEqual(set(rows), {"columbine"})
        self.assertEqual(rows["columbine"]["temperature_c"], 16.51)
        self.assertEqual(rows["columbine"]["valid_utc"], "2026-11-09T12:00:00Z")
        with self.assertRaises(ValueError):
            marine.sample_sst(data.getvalue(), NOW.date(), "test-source")

    def test_missing_wave_retains_original_while_sst_updates_only_at_named_coast(self):
        name = marine.AREAS["saldanha"][0]
        prior = {"valid_utc": "2026-11-09T03:00:00Z", "swell_height_m": 1.2}
        previous = {"towns": {"langebaan": {"area_name": name, "wave": prior}}}
        sample = {"valid_utc": stamp(NOW), "latitude": -33.125, "longitude": 17.625, "temperature_c": 17}
        with (patch.object(marine, "read_sst", return_value=({}, {"saldanha": sample})),
              patch.object(marine, "read_waves", side_effect=OSError("no waves")), redirect_stderr(io.StringIO())):
            result = marine.update_marine(previous, now=NOW)
        self.assertEqual(result["towns"]["langebaan"]["wave"], prior)
        self.assertEqual(result["towns"]["langebaan"]["sst"]["distance_km"], 38.17)
        self.assertIn("Offshore", result["towns"]["langebaan"]["area_name"])
        self.assertNotIn("malmesbury", result["towns"])
        self.assertEqual(result["refresh_errors"], ["wave"])


if __name__ == "__main__":
    unittest.main()
