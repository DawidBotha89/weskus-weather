"""Verify the units and forward rain periods returned by the public MET comparison."""
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from baseline import read_town


class BaselineTests(unittest.TestCase):
    def test_preserve_direction_pressure_and_forward_rain_period(self):
        step = {"time": "2026-11-10T12:00:00Z", "kmh": 36, "fromDeg": 360,
                "pressureHpa": 1013.2, "rainMm": 0, "rainPeriodH": 1}
        report = {"updated": step["time"], "series": [step, {**step, "rainMm": 3.2, "rainPeriodH": 6},
                  {**step, "rainPeriodH": 4, "fromDeg": -5, "pressureHpa": False}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(report).encode())):
            rows = read_town(-32.8, 18.0)["hours"]
        self.assertEqual((rows[0]["wind_speed_ms"], rows[0]["wind_from_deg"], rows[0]["pressure_hpa"]), (10, 0, 1013.2))
        self.assertEqual([(r["rain_mm"], r["rain_period_h"]) for r in rows], [(0, 1), (3.2, 6), (None, None)])
        self.assertIsNone(rows[2]["wind_from_deg"])
        self.assertIsNone(rows[2]["pressure_hpa"])
