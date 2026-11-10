import unittest
from datetime import datetime, timezone, timedelta
from verification import score


class ProspectiveScores(unittest.TestCase):
    def test_first_pair_only_before_validity_and_with_local_observation(self):
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        valid = now + timedelta(hours=9)
        row = {"valid_utc": valid.isoformat(), "temperature_c": 17, "wind_speed_ms": 4.5}
        baseline = {"updated_utc": now.isoformat(), "fetched_utc": now.isoformat(),
                    "hours": [{**row, "temperature_c": 14}]}
        feed = {"atmosphere": {"run_utc": now.isoformat(), "towns": {"a": [row]}},
                "baseline": {"towns": {"a": baseline}}, "stations": {"towns": {}}}
        ledger = {}
        self.assertEqual(score(feed, ledger, now)["towns"]["a"]["n"], 0)
        row["temperature_c"] = 999
        self.assertEqual(score(feed, ledger, now)["towns"]["a"]["n"], 0)
        obs = {"station_id": "gauge", "observed_utc": valid.isoformat(), "temperature_c": 15,
               "wind_speed_ms": None, "distance_km": 18}
        feed["stations"]["history"] = {"a": [obs]}
        self.assertEqual(score(feed, ledger, valid)["towns"]["a"]["n"], 0)
        obs["distance_km"] = 4
        summary = score(feed, ledger, valid)["towns"]["a"]
        self.assertEqual((summary["n"], summary["own_temp_mae_c"], summary["baseline_temp_mae_c"]), (1, 2, 1))
        self.assertIsNone(summary["baseline_wind_mae_ms"])
        self.assertEqual(score(feed, ledger, valid)["towns"]["a"], summary)
        self.assertEqual(score(feed, {}, valid)["towns"], {})
        self.assertEqual(score(feed, ledger, valid + timedelta(days=31))["towns"]["a"]["n"], 0)
