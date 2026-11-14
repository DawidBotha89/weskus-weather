"""Synthetic boundary tests; these do not establish real estuary tide skill."""
from copy import deepcopy
import csv
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

import estuary_observations as obs
import estuary_trial as trial
from point_sources import stamp

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
URL = "https://example.test/research-observations"
HEADERS = ["reach_id", "river_name", "p_lat", "p_lon", "time_str", "wse", "wse_u",
           "reach_q", "reach_q_b", "cycle_id", "pass_id", "wse_units", "wse_u_units", "p_lat_units", "p_lon_units"]


def csv_row(**changes):
    row = dict(zip(HEADERS, [obs.REACH, "Berg", obs.POSITION[0], obs.POSITION[1],
                            "2026-11-15T11:00:00Z", .5, .05, 1, 256, 42, 12, "m", "m", "degrees_north", "degrees_east"]))
    row.update(changes)
    return row


def response(rows, headers=HEADERS):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return json.dumps({"status": "200 OK", "results": {"csv": stream.getvalue()}}).encode()


def model():
    return {"model_version": "synthetic-v1", "created_utc": stamp(NOW),
            "expires_utc": stamp(NOW+timedelta(days=2)), "training_until_utc": "2025-12-31T00:00:00Z",
            "reach_id": obs.REACH, "collection": obs.COLLECTION, "datum": obs.DATUM,
            "latitude": obs.POSITION[0], "longitude": obs.POSITION[1],
            "gain": .8, "offset_m": .5, "coast_offset_m": .2, "training_mean_m": .7,
            "lag_minutes": 55, "boundary_sha256": "test-boundary",
            "boundary_implementation_sha256": "test-implementation",
            "boundary_predictor_version": "@neaps/tide-predictor@0.11.0"}


def boundary_stub(times, frozen):
    return [[2., 3.] for _ in times]


class ObservationTests(unittest.TestCase):
    def test_units_datum_quality_and_response_provenance(self):
        blob = response([csv_row(reach_q=q, time_str=f"2026-11-15T0{q}:00:00Z") for q in range(4)])
        rows = obs.decode(blob, URL, NOW)
        self.assertEqual([r["quality_label"] for r in rows], ["good", "suspect", "degraded", "bad"])
        for row in rows:
            self.assertEqual((row["level_m"], row["uncertainty_m"]), (.5, .05))
            self.assertEqual((row["datum"], row["collection"]), ("EGM2008 geoid", obs.COLLECTION))
            self.assertEqual(row["source_url"], URL)
            self.assertEqual(row["first_seen_utc"], stamp(NOW))
            self.assertEqual(row["response_sha256"], hashlib.sha256(blob).hexdigest())
            self.assertEqual((row["quality_bits"], row["cycle"], row["pass"]), ("256", "42", "12"))

    def test_bad_identity_units_position_quality_and_timestamps_are_rejected(self):
        cases = [{"reach_id": "other"}, {"wse_units": "cm"}, {"wse_u_units": "ft"},
                 {"p_lat": obs.POSITION[0]+.01}, {"p_lon": "nan"}, {"reach_q": 4}, {"p_lat_units": "degrees_east"},
                 {"time_str": "2026-11-15T12:00:01Z"}, {"time_str": "2026-11-15T11:00:00"}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                obs.decode(response([csv_row(**change)]), URL, NOW)
        # Offset timestamps are normalized to UTC, not silently treated as local.
        row = obs.decode(response([csv_row(time_str="2026-11-15T13:00:00+02:00")]), URL, NOW)[0]
        self.assertEqual(row["observed_utc"], "2026-11-15T11:00:00Z")

    def test_fill_values_are_not_measurements_and_duplicates_need_review(self):
        for change in ({"wse": -999999999999}, {"wse": "nan"}, {"wse_u": "inf"},
                       {"wse_u": 0}, {"wse_u": 1}):
            with self.subTest(change=change):
                self.assertEqual(obs.decode(response([csv_row(**change)]), URL, NOW), [])
        with self.assertRaises(ValueError):
            obs.decode(response([csv_row(), csv_row(wse=.7)]), URL, NOW)
        for blob in [b'{}', *(response([csv_row()], [x for x in HEADERS if x != missing])
                             for missing in HEADERS)]:
            with self.assertRaises(ValueError):
                obs.decode(blob, URL, NOW)

    def test_collection_retention_first_seen_and_latest_quality(self):
        first = obs.decode(response([csv_row()]), URL, NOW-timedelta(minutes=30))[0]
        old = {"collection": obs.COLLECTION, "history": [first,
               {**first, "observed_utc": stamp(NOW-timedelta(days=181))},
               {**first, "observed_utc": "2026-11-15T10:00:00Z", "collection": "unreviewed"}]}
        original = deepcopy(old)
        blob = response([csv_row(wse=.6), csv_row(time_str=stamp(NOW), reach_q=2, wse=1.2)])
        with patch.object(obs, "fetch", return_value=(blob, {})):
            result = obs.collect(old, NOW)
        self.assertEqual(old, original)
        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(result["latest"]["observed_utc"], first["observed_utc"])
        self.assertEqual(result["latest"]["first_seen_utc"], first["first_seen_utc"])
        self.assertEqual(result["latest"]["level_m"], .6)
        self.assertEqual(result["fetched_utc"], stamp(NOW))

    def test_six_hour_cache_and_failed_fetch_do_not_invent_freshness(self):
        old = {"collection": obs.COLLECTION, "fetched_utc": stamp(NOW-timedelta(hours=5)), "history": []}
        with patch.object(obs, "fetch", side_effect=AssertionError("cached source downloaded")):
            self.assertIs(obs.collect(old, NOW), old)
        old["fetched_utc"] = stamp(NOW-timedelta(hours=6))
        original = deepcopy(old)
        with patch.object(obs, "fetch", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                obs.collect(old, NOW)
        self.assertEqual(old, original)


class TrialTests(unittest.TestCase):
    def test_freezes_only_six_to_thirty_hour_leads_and_preserves_issued_values(self):
        ledger, frozen = {}, model()
        now = NOW+timedelta(seconds=1)
        with patch.object(trial, "boundary", side_effect=boundary_stub):
            trial.freeze(ledger, frozen, now)
        rows = ledger[frozen["model_version"]]["records"]
        self.assertEqual(min(rows), "2026-11-15T18:05:00Z")
        self.assertTrue(all(timedelta(hours=6) <= trial.instant(t)-now <= timedelta(hours=30) for t in rows))
        first = deepcopy(rows)
        self.assertEqual(next(iter(first.values()))["values"], [2.9, 2.2, .7])
        with patch.object(trial, "boundary", return_value=[]) as coast:
            trial.freeze(ledger, frozen, now)
        coast.assert_not_called()
        self.assertEqual(rows, first)
        with patch.object(trial, "boundary", side_effect=lambda times, m: [[9., 9.] for _ in times]):
            trial.freeze(ledger, frozen, now+timedelta(hours=1))
        self.assertTrue(all(rows[key] == value for key, value in first.items()))
        changed = {**frozen, "gain": .9}
        with self.assertRaises(ValueError):
            trial.freeze(ledger, changed, now)

    def test_no_historical_backfill_and_left_bin_preserves_native_prediction(self):
        ledger, frozen = {}, model()
        with patch.object(trial, "boundary", side_effect=boundary_stub):
            trial.freeze(ledger, frozen, NOW)
        records = ledger[frozen["model_version"]]["records"]
        self.assertIsNone(trial.matched_prediction(records, NOW-timedelta(hours=1)))
        self.assertEqual(trial.matched_prediction(records, NOW+timedelta(hours=6, minutes=2, seconds=11)), [2.9, 2.2, .7])
        for delta in (timedelta(hours=5, minutes=59), timedelta(hours=30, seconds=1)):
            self.assertIsNone(trial.matched_prediction(records, NOW+delta))
        observations = obs.decode(response([csv_row()]), URL, NOW)
        summary = trial.scores(ledger, {"history": observations}, NOW)
        self.assertEqual(summary[frozen["model_version"]]["groups"]["suspect_only"]["n"], 0)
        self.assertEqual(ledger[frozen["model_version"]]["matches"], {})

    def test_scores_separate_quality_and_reject_wrong_datum_reach_position_collection(self):
        ledger, frozen = {}, model()
        with patch.object(trial, "boundary", side_effect=boundary_stub):
            trial.freeze(ledger, frozen, NOW)
        later = NOW+timedelta(hours=10)
        good = obs.decode(response([csv_row(time_str=stamp(NOW+timedelta(hours=6)), reach_q=0, wse=1)]), URL, later)[0]
        suspect = {**good, "observed_utc": stamp(NOW+timedelta(hours=7)), "quality": 1, "level_m": 2}
        rejected = [{**good, "observed_utc": stamp(NOW+timedelta(hours=8)), **change} for change in (
            {"datum": "chart datum"}, {"collection": "old release"}, {"reach_id": "other"},
            {"latitude": good["latitude"]+.001}, {"longitude": good["longitude"]+.001},
            {"quality": 2}, {"quality": 3}, {"level_m": float("nan")})]
        future = {**good, "observed_utc": stamp(later+timedelta(hours=1))}
        source = {"history": [good, suspect, *rejected, future]}
        summary = trial.scores(ledger, source, later)[frozen["model_version"]]
        self.assertEqual(summary["groups"]["good_only"]["n"], 1)
        self.assertEqual(summary["groups"]["suspect_only"]["n"], 1)
        self.assertEqual(summary["groups"]["good_only"]["candidate"], {"mae_m": 1.9, "rmse_m": 1.9})
        self.assertEqual(summary["groups"]["suspect_only"]["candidate"]["mae_m"], .9)
        self.assertEqual(len(ledger[frozen["model_version"]]["matches"]), 2)
        matches = deepcopy(ledger[frozen["model_version"]]["matches"])
        source["history"][0]["level_m"] = 19
        summary = trial.scores(ledger, {"history": source["history"][:2]}, later+timedelta(hours=1))[frozen["model_version"]]
        stored = ledger[frozen["model_version"]]["matches"][good["observed_utc"]]
        self.assertEqual(stored["observed"], matches[good["observed_utc"]]["observed"])
        self.assertEqual(stored["predicted"], matches[good["observed_utc"]]["predicted"])
        self.assertEqual(stored["excluded_reason"], "source_revised_after_first_match")
        self.assertEqual(summary["excluded_revisions"], 1)
        self.assertEqual(summary["groups"]["good_only"]["n"], 0)

    def test_each_identity_revision_excludes_original_match_without_rewriting_it(self):
        ledger, frozen = {}, model()
        with patch.object(trial, "boundary", side_effect=boundary_stub):
            trial.freeze(ledger, frozen, NOW)
        later = NOW+timedelta(hours=8)
        row = obs.decode(response([csv_row(time_str=stamp(NOW+timedelta(hours=6)), reach_q=0)]), URL, later)[0]
        trial.scores(ledger, {"history": [row]}, later)
        original = deepcopy(ledger[frozen["model_version"]]["matches"][row["observed_utc"]])
        for field, value in (("level_m", 1), ("quality", 3), ("quality_bits", "1024"),
                             ("datum", "chart datum"), ("collection", "unreviewed"), ("reach_id", "other"),
                             ("latitude", row["latitude"]+.01), ("longitude", row["longitude"]+.01)):
            with self.subTest(field=field):
                isolated = deepcopy(ledger)
                summary = trial.scores(isolated, {"history": [{**row, field: value}]}, later)[frozen["model_version"]]
                record = isolated[frozen["model_version"]]["matches"][row["observed_utc"]]
                self.assertEqual(record, {**original, "excluded_reason": "source_revised_after_first_match"})
                self.assertEqual(summary["groups"]["good_only"]["n"], 0)
                self.assertEqual(summary["excluded_revisions"], 1)

    def test_model_creation_and_expiry_prevent_new_forecasts_but_retain_scores(self):
        frozen = model()
        for now in (NOW-timedelta(seconds=1), trial.instant(frozen["expires_utc"])):
            ledger = {}
            with patch.object(trial, "boundary", side_effect=AssertionError("inactive model evaluated")):
                trial.freeze(ledger, frozen, now)
            self.assertEqual(ledger[frozen["model_version"]]["records"], {})
        ledger = {}
        with patch.object(trial, "boundary", side_effect=boundary_stub):
            trial.freeze(ledger, frozen, NOW)
        original = deepcopy(ledger)
        with patch.object(trial, "boundary", side_effect=AssertionError("expired model evaluated")):
            trial.freeze(ledger, frozen, NOW+timedelta(days=3))
        self.assertEqual(ledger, original)

    def test_boundary_pins_data_and_rejects_incomplete_nonfinite_node_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"data").mkdir()
            (root/"data/tide-stations.json").write_bytes(b"{}")
            (root/"coastal_boundary.cjs").write_bytes(b"// synthetic helper")
            package = root/"node_modules/@neaps/tide-predictor/package.json"
            package.parent.mkdir(parents=True)
            package.write_text('{"version":"0.11.0"}', encoding="utf-8")
            frozen = {**model(), "boundary_sha256": hashlib.sha256(b"{}").hexdigest(),
                      "boundary_implementation_sha256": hashlib.sha256(b"// synthetic helper").hexdigest()}
            times = [stamp(NOW)]
            with patch.object(trial, "ROOT", root):
                for levels in ([], [[1]], [[1, float("nan")]]):
                    result = subprocess.CompletedProcess([], 0, json.dumps({"levels": levels}), "")
                    with patch.object(trial.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                        trial.boundary(times, frozen)
                (root/"data/tide-stations.json").write_bytes(b"changed")
                with patch.object(trial.subprocess, "run") as node, self.assertRaises(ValueError):
                    trial.boundary(times, frozen)
                node.assert_not_called()
                (root/"data/tide-stations.json").write_bytes(b"{}")
                (root/"coastal_boundary.cjs").write_bytes(b"changed implementation")
                with patch.object(trial.subprocess, "run") as node, self.assertRaises(ValueError):
                    trial.boundary(times, frozen)
                node.assert_not_called()
                (root/"coastal_boundary.cjs").write_bytes(b"// synthetic helper")
                package.write_text('{"version":"0.12.0"}', encoding="utf-8")
                with patch.object(trial.subprocess, "run") as node, self.assertRaises(ValueError):
                    trial.boundary(times, frozen)
                node.assert_not_called()

    def test_three_source_failures_preserve_original_source_and_success_timestamp(self):
        frozen = model()
        source = {"collection": obs.COLLECTION, "fetched_utc": stamp(NOW-timedelta(days=1)), "history": [], "latest": None,
                  "source_url": "https://example.test/original"}
        old = {"berg": {"observations": source}, "health": {"last_success_utc": stamp(NOW-timedelta(days=1))}}
        original = deepcopy(old)
        with (patch.object(trial.Path, "read_text", return_value=json.dumps(frozen)),
              patch.object(trial.observations, "collect", side_effect=OSError("source unavailable")) as collect,
              patch.object(trial.time, "sleep") as sleep,
              patch.object(trial, "boundary", side_effect=boundary_stub)):
            result = trial.update(old, {}, NOW)
        self.assertEqual(collect.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(20), call(60)])
        self.assertEqual(result["berg"]["observations"], source)
        self.assertEqual(result["health"], {"status": "degraded", "error": "observation_refresh_failed", "attempts": 3,
                                             "last_success_utc": old["health"]["last_success_utc"]})
        self.assertEqual(old, original)
        self.assertFalse(result["default_changed"])
        self.assertFalse(result["langebaan"]["candidate_available"])

    def test_source_recovers_on_second_attempt_and_expired_model_emits_no_hours(self):
        frozen = model()
        now = trial.instant(frozen["expires_utc"])
        source = {"collection": obs.COLLECTION, "fetched_utc": stamp(now), "history": [], "latest": None}
        with (patch.object(trial.Path, "read_text", return_value=json.dumps(frozen)),
              patch.object(trial.observations, "collect", side_effect=[OSError("once"), source]),
              patch.object(trial.time, "sleep") as sleep,
              patch.object(trial, "boundary", side_effect=AssertionError("expired"))):
            result = trial.update({}, {}, now)
        sleep.assert_called_once_with(20)
        self.assertEqual(result["health"], {"status": "complete", "error": None, "attempts": 2, "last_success_utc": stamp(now)})
        self.assertEqual(result["berg"]["hours"], [])
        self.assertEqual(result["berg"]["scores"]["groups"]["good_only"]["candidate"]["mae_m"], None)


if __name__ == "__main__":
    unittest.main()
