from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import atmosphere as a


class AtmosphereTests(unittest.TestCase):
    def test_grid_origin_and_stencil(self):
        for west in (0, 180):
            stencil = a.grid_stencil(-32.811, 17.893, west)
            self.assertAlmostEqual(sum(w for _, w in stencil), 1)
            lon = sum(((west + idx % 1440 / 4) % 360) * w for idx, w in stencil)
            lat = sum((90 - (idx // 1440) / 4) * w for idx, w in stencil)
            self.assertAlmostEqual(lat, -32.811)
            self.assertAlmostEqual(lon, 17.893)

    def test_interval_and_units(self):
        valid = datetime(2026, 11, 10, tzinfo=timezone.utc)
        fields = {"2t": 293.15, "2d": 283.15, "10u": -2.0, "10v": 0.0,
                  "tcc": .5, "tp": .004, "msl": 101325}
        row = a.build_row(fields, valid, 3, 1)
        self.assertEqual(row["wind_from_deg"], 90)
        self.assertEqual(row["temperature_c"], 20)
        self.assertEqual(row["humidity_pct"], 52.5)
        self.assertEqual(row["pressure_hpa"], 1013.25)
        self.assertEqual(row["rain_mm"], 3)
        self.assertEqual(a.build_row(fields, valid, 3, 4.03)["rain_mm"], 0)
        self.assertIsNone(a.build_row(fields, valid, 3, 15)["rain_mm"])
        self.assertIsNone(a.build_row(fields, valid, None, None)["rain_mm"])
        fields.update({"10u": 0, "2d": 293.15})
        row = a.build_row(fields, valid, 3, 0)
        self.assertIsNone(row["wind_from_deg"])
        self.assertEqual(row["humidity_pct"], 100)

    def test_reuse_cached_cycle_and_reject_incomplete_payload(self):
        instant = datetime(2026, 11, 10, tzinfo=timezone.utc)
        values = {key: {slug: number for slug in a.TOWNS} for key, number in
                  {"2t": 290, "2d": 283, "10u": 1, "10v": 2, "msl": 101000, "tp": 0, "tcc": .1}.items()}
        with patch.object(a, "datetime", wraps=datetime) as clock, patch.object(a, "read_source"), \
                patch.object(a, "field_groups"), patch.object(a, "read_step", return_value=values):
            clock.now.return_value = instant
            payload = a.update_forecast(None)
        self.assertTrue(a.complete(payload, instant))
        with patch.object(a, "datetime", wraps=datetime) as clock, \
                patch.object(a, "read_source", side_effect=AssertionError("cached run performed network IO")):
            clock.now.return_value = instant
            self.assertIs(a.update_forecast(payload), payload)
        self.assertEqual(len(payload["towns"]), 15)
        rows = payload["towns"]["langebaan"]
        self.assertEqual(len(rows), 53)
        self.assertEqual(rows[48]["rain_interval_hours"], 3)
        self.assertEqual(rows[49]["rain_interval_hours"], 6)
        rows.pop()
        self.assertFalse(a.complete(payload, instant))


if __name__ == "__main__":
    unittest.main()
