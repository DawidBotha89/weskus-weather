"""Failure recovery and publication health across cached and partial source responses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import atmosphere
import update


NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def fixtures():
    rows = [{"valid_utc": update.utc(NOW + timedelta(hours=lead)), "temperature_c": 20,
             "wind_speed_ms": 3, "wind_from_deg": 180, "humidity_pct": 60, "cloud_pct": 20,
             "pressure_hpa": 1015, "rain_mm": None if lead == 0 else 0,
             "rain_interval_hours": None if lead == 0 else 3 if lead <= 144 else 6}
            for lead in atmosphere.STEPS]
    air = {"model": atmosphere.MODEL, "run_utc": update.utc(NOW), "fetched_utc": update.utc(NOW),
           "towns": {slug: deepcopy(rows) for slug in update.TOWNS}}
    baseline = {"towns": {slug: {"hours": deepcopy(rows), "updated_utc": update.utc(NOW),
                                 "fetched_utc": update.utc(NOW), "stale": False} for slug in update.TOWNS},
                "refresh_errors": []}
    marine = {"towns": {slug: {"wave": {"valid_utc": update.utc(NOW)},
                              "sst": {"valid_utc": update.utc(NOW - timedelta(days=1))}}
                       for slug in update.COASTAL_TOWNS}}
    return {"atmosphere": air, "baseline": baseline, "marine": marine,
            "stations": {"towns": {}, "history": {}}}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(update.dt, "datetime", wraps=datetime).start()
        self.clock.now.return_value = NOW
        self.sleep = patch.object(update.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_retry_mutation_isolation_and_partial_progress(self):
        previous = fixtures()["baseline"]
        previous_copy = deepcopy(previous)
        calls = []
        slug = next(iter(update.TOWNS))

        def collect(old):
            calls.append(deepcopy(old))
            if len(calls) == 1:
                old["towns"][slug]["hours"][0]["temperature_c"] = 999
                raise OSError("transient connection failure")
            if len(calls) == 2:
                partial = deepcopy(previous)
                partial["towns"][slug]["hours"][0]["temperature_c"] = 22
                return {"towns": {slug: partial["towns"][slug]}}
            self.assertEqual(old["towns"][slug]["hours"][0]["temperature_c"], 22)
            return {**old, "refresh_errors": []}

        result, count, errors = update.refresh("baseline", collect, previous)
        self.assertEqual((count, errors), (3, []))
        self.assertEqual(previous, previous_copy)
        self.assertEqual(calls[1]["towns"][slug]["hours"][0]["temperature_c"], 20)
        self.assertEqual(len(result["towns"]), 15)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [20, 60])

    def test_degraded_publication_retains_last_success_dates_and_frozen_pairs(self):
        feed = fixtures()
        last_success = update.utc(NOW - timedelta(hours=1))
        feed["health"] = {"last_success_utc": last_success}
        slug = next(iter(update.STATIONS))
        feed["stations"]["towns"][slug] = {
            "station_id": update.STATIONS[slug][0], "distance_km": 4,
            "observed_utc": update.utc(NOW - timedelta(hours=8))}
        ledger = {}
        update.score(feed, ledger, NOW)
        original_pairs = deepcopy(ledger)
        collectors = {name: (lambda old, result=result: deepcopy(result))
                      for name, result in fixtures().items()}
        collectors["stations"] = lambda old: deepcopy(feed["stations"])
        def failed(old):
            raise OSError("upstream unavailable")
        collectors["baseline"] = failed
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            update.write(folder / "latest.json", feed)
            update.write(folder / "verification.json", ledger)
            signal = folder / "outputs"
            with patch.dict(update.COLLECTORS, collectors, clear=True), \
                    patch.dict(update.os.environ, {"GITHUB_OUTPUT": str(signal)}):
                self.assertEqual(update.main(folder), 1)
            published = update.load(folder / "latest.json")
            self.assertEqual(published["baseline"], feed["baseline"])
            self.assertEqual(published["health"]["last_success_utc"], last_success)
            self.assertEqual(published["health"]["attempts"]["baseline"], 3)
            self.assertEqual(published["health"]["status"], "degraded")
            self.assertEqual(published["refresh_errors"], ["baseline"])
            self.assertIn("observation_stale", [w["code"] for w in published["health"]["warnings"]])
            self.assertEqual(update.load(folder / "verification.json"), original_pairs)
            self.assertEqual(signal.read_text(), "snapshot_written=true\n")

    def test_healthy_sources_and_quiet_stations_advance_success_once(self):
        feed = fixtures()
        collectors = {name: (lambda old, result=result: deepcopy(result)) for name, result in feed.items()}
        with TemporaryDirectory() as directory, patch.dict(update.COLLECTORS, collectors, clear=True):
            folder = Path(directory)
            self.assertEqual(update.main(folder), 0)
            health = update.load(folder / "latest.json")["health"]
            self.assertEqual(health["status"], "complete")
            self.assertEqual(health["attempted_utc"], update.utc(NOW))
            self.assertEqual(health["last_success_utc"], update.utc(NOW))
            self.assertEqual(health["attempts"], dict.fromkeys(collectors, 1))
            self.assertEqual(len(health["warnings"]), len(update.STATIONS))
            self.sleep.assert_not_called()

    def test_partial_baseline_never_passes_just_because_cache_has_all_towns(self):
        previous = fixtures()["baseline"]
        slug = next(iter(update.TOWNS))
        result, count, errors = update.refresh("baseline", lambda old: {"towns": {slug: previous["towns"][slug]}}, previous)
        self.assertEqual(count, 3)
        self.assertIn("incomplete_town_coverage", errors)
        self.assertEqual(len(result["towns"]), 15)

    def test_failed_scoring_does_not_signal_publication_or_overwrite_ledger(self):
        feed = fixtures()
        collectors = {name: (lambda old, result=result: deepcopy(result)) for name, result in feed.items()}
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            update.write(folder / "latest.json", feed)
            update.write(folder / "verification.json", {"untouched": {}})
            original = (folder / "latest.json").read_bytes()
            with patch.dict(update.COLLECTORS, collectors, clear=True), \
                    patch.object(update, "score", side_effect=ValueError("invalid ledger")), \
                    patch.dict(update.os.environ, {"GITHUB_OUTPUT": str(folder / "outputs")}):
                with self.assertRaises(ValueError):
                    update.main(folder)
            self.assertEqual((folder / "latest.json").read_bytes(), original)
            self.assertEqual(update.load(folder / "verification.json"), {"untouched": {}})
            self.assertFalse((folder / "outputs").exists())

    def test_freshness_limits_apply_to_reused_source_dates(self):
        sources = fixtures()
        sources["atmosphere"]["run_utc"] = update.utc(NOW - timedelta(hours=25))
        self.assertIn("stale_or_future_model_run", update.source_errors("atmosphere", sources["atmosphere"], NOW))
        slug = next(iter(update.TOWNS))
        sources["baseline"]["towns"][slug]["fetched_utc"] = update.utc(NOW - timedelta(hours=3))
        self.assertIn("stale_baseline:" + slug, update.source_errors("baseline", sources["baseline"], NOW))

    def test_empty_sources_and_invalid_marine_date_cannot_hide_behind_cache(self):
        sources = fixtures()
        for name in ("baseline", "marine"):
            with self.subTest(name=name):
                result, count, errors = update.refresh(name, lambda old: {"towns": {}}, sources[name])
                self.assertEqual(count, 3)
                self.assertTrue(errors)
                self.assertEqual(result["towns"], sources[name]["towns"])
        invalid = deepcopy(sources["marine"])
        slug = next(iter(invalid["towns"]))
        invalid["towns"][slug]["sst"]["valid_utc"] = "not-a-date"
        result, count, errors = update.refresh("marine", lambda old: invalid, sources["marine"])
        self.assertEqual((count, errors), (3, ["collector_failed"]))
        self.assertEqual(result, sources["marine"])


if __name__ == "__main__":
    unittest.main()
