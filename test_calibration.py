"""Synthetic regression checks for timing eligibility and candidate isolation."""

import copy
from datetime import UTC, datetime, timedelta
import unittest

from calibration import STATION, calibrated_candidate, fit_model, utc
from research_calibration import matched_samples


class CalibrationTests(unittest.TestCase):
    def samples(self):
        rows = []
        for hour in (3, 9, 15, 21):
            valid = datetime(2026, 11, 12, hour, tzinfo=UTC)
            rows.append({"station_id": STATION, "valid_utc": utc(valid),
                         "saved_utc": utc(valid - timedelta(hours=20)),
                         "available_utc": utc(valid + timedelta(minutes=30)),
                         "archived_utc": utc(valid + timedelta(hours=1)),
                         "temperature_c": 20, "wind_speed_ms": 10,
                         "actual_temperature_c": 10 if hour in (3, 21) else 30,
                         "actual_wind_speed_ms": 1})
        return rows

    def model(self):
        return fit_model(self.samples(), "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z")

    def test_bounds_and_later_measurements_cannot_change_fit(self):
        base = self.model()
        self.assertEqual(base["parameters"]["day"]["temperature_offset_c"], 4)
        self.assertEqual(base["parameters"]["night"]["temperature_offset_c"], -4)
        self.assertEqual(base["parameters"]["night"]["wind_factor"], .5)
        later = copy.deepcopy(self.samples())
        for row in later:
            row["valid_utc"] = row["valid_utc"].replace("11-12", "11-14")
            row["actual_temperature_c"] = -40
        shifted = fit_model(self.samples() + later, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z")
        self.assertEqual(base, shifted)

    def test_unavailable_or_duplicate_training_measurements_are_rejected(self):
        rows = self.samples()
        rows[0]["archived_utc"] = "2026-11-13T01:00:00Z"
        with self.assertRaises(ValueError):
            fit_model(rows, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z")
        with self.assertRaises(ValueError):
            fit_model(self.samples() + self.samples(), "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z")

    def test_candidate_contains_only_calibrated_weather_and_preserves_raw(self):
        forecast = {"run_utc": "2026-11-13T00:00:00Z", "towns": {"langebaan": [
            {"valid_utc": "2026-11-13T09:00:00Z", "temperature_c": 20, "wind_speed_ms": 10,
             "humidity_pct": 80, "rain_mm": 1},
            {"valid_utc": "2026-11-15T09:00:00Z", "temperature_c": 20, "wind_speed_ms": 10}]}}
        original = copy.deepcopy(forecast)
        result = calibrated_candidate(forecast, self.model(), datetime(2026, 11, 13, 1, tzinfo=UTC))
        self.assertEqual(forecast, original)
        self.assertEqual(len(result["towns"]["langebaan"]), 1)
        row = result["towns"]["langebaan"][0]
        self.assertEqual((row["temperature_c"], row["wind_speed_ms"]), (24, 5))
        self.assertNotIn("humidity_pct", row)
        self.assertNotIn("rain_mm", row)
        self.assertEqual(row["station_id"], STATION)
        self.assertEqual(result["target"]["distance_km"], 12.71)

    def test_model_is_unavailable_before_creation_and_after_expiry(self):
        for now in (datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 21, tzinfo=UTC)):
            self.assertFalse(calibrated_candidate({}, self.model(), now)["towns"])
        self.assertFalse(calibrated_candidate({}, None)["towns"])

    def test_pilot_only_emits_observed_time_slots_and_rejects_stale_runs(self):
        start = datetime(2026, 11, 13, tzinfo=UTC)
        rows = [{"valid_utc": utc(start + timedelta(hours=h)), "temperature_c": 20, "wind_speed_ms": 4}
                for h in range(25)]
        rows.append({"valid_utc": "2026-11-13T09:01:00Z", "temperature_c": 20, "wind_speed_ms": 4})
        forecast = {"run_utc": utc(start), "towns": {"langebaan": rows}}
        result = calibrated_candidate(forecast, self.model(), start)
        self.assertEqual([row["valid_utc"] for row in result["towns"]["langebaan"]],
                         [utc(start + timedelta(hours=h)) for h in (3, 9, 15, 21)])
        self.assertEqual(result["supported_sast_hours"], [5, 11, 17, 23])
        forecast["run_utc"] = utc(start - timedelta(hours=24, seconds=1))
        self.assertFalse(calibrated_candidate(forecast, self.model(), start)["towns"])

    def test_archive_join_requires_actual_station_and_values(self):
        row = self.samples()[0]
        ledger = {"langebaan": {row["valid_utc"]: {
            "saved": row["saved_utc"], "station": STATION,
            "pairs": {"temp": [20, 21], "wind": [10, 11]}, "actual": {"temp": 10, "wind": 1}}}}
        obs = {"station_id": STATION, "observed_utc": row["valid_utc"], "distance_km": 12.71,
               "available_utc": row["available_utc"], "temperature_c": 10, "wind_speed_ms": 1}
        history = [{"generated": row["archived_utc"], "stations": {"towns": {"langebaan": obs}}}]
        samples, excluded = matched_samples(ledger, history)
        self.assertEqual(len(samples), 1)
        self.assertFalse(excluded)
        obs["temperature_c"] = 11
        with self.assertRaises(ValueError):
            matched_samples(ledger, history)
        self.assertEqual(len(matched_samples(ledger, [])[1]), 1)


if __name__ == "__main__":
    unittest.main()
