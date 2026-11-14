"""Regression checks for the separately frozen prospective calibration trial."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
import unittest

from calibration import STATION, TARGET, utc
from candidate_verification import score_candidate
from verification import score


NOW = datetime(2026, 11, 14, 20, tzinfo=UTC)
VERSION = "geelbek-test-v1"
VALID = NOW + timedelta(hours=9)


def fixture(now=NOW):
    raw = {"valid_utc": utc(VALID), "temperature_c": 20, "wind_speed_ms": 8}
    met = {**raw, "temperature_c": 21, "wind_speed_ms": 10}
    trial = {**raw, "temperature_c": 17, "wind_speed_ms": 4,
             "issued_utc": utc(now), "model_version": VERSION, "station_id": STATION,
             "training_cutoff_utc": utc(NOW - timedelta(hours=2))}
    return {
        "atmosphere": {"run_utc": utc(NOW), "towns": {"langebaan": [raw]}},
        "baseline": {"towns": {"langebaan": {"hours": [met], "updated_utc": utc(now),
                                             "fetched_utc": utc(now)}}},
        "stations": {"towns": {}, "history": {}},
        "calibration": {"status": "experimental station proxy", "target": deepcopy(TARGET),
                        "model_version": VERSION, "training_cutoff_utc": trial["training_cutoff_utc"],
                        "expires_utc": utc(NOW + timedelta(days=7)),
                        "towns": {"langebaan": [trial]}}}


def report(when=VALID, station=STATION):
    return {"station_id": station, "observed_utc": when.isoformat(),
            "latitude": TARGET["latitude"], "longitude": TARGET["longitude"],
            "distance_km": 12.71, "temperature_c": 16, "wind_speed_ms": 3,
            "source_url": "https://example.test/observed-geelbek"}


class CandidateScores(unittest.TestCase):
    def test_normal_microsecond_clock_accepts_own_second_precision_issue(self):
        now = NOW.replace(microsecond=123456)
        ledger = {}
        summary = score_candidate(fixture(now), ledger, now)["models"][VERSION]
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(len(ledger[VERSION]["records"]), 1)

    def test_first_saved_triple_is_immutable_across_new_forecasts_and_retry(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        frozen = deepcopy(ledger)
        feed["atmosphere"]["towns"]["langebaan"][0]["temperature_c"] = 99
        feed["baseline"]["towns"]["langebaan"]["hours"][0]["temperature_c"] = 98
        feed["calibration"]["towns"]["langebaan"][0]["temperature_c"] = 97
        feed["calibration"]["towns"]["langebaan"][0]["issued_utc"] = utc(NOW + timedelta(minutes=1))
        score_candidate(feed, ledger, NOW + timedelta(minutes=1))
        self.assertEqual(ledger, frozen)
        entry = next(iter(ledger[VERSION]["records"].values()))
        self.assertEqual(entry["pairs"], {"temp": [20, 21, 17], "wind": [8, 10, 4]})

    def test_existing_two_way_ledger_is_never_backfilled_into_candidate_trial(self):
        original_feed, original_ledger = fixture(), {}
        original_feed["calibration"] = {}
        score(original_feed, original_ledger, NOW - timedelta(hours=1))
        original_copy = deepcopy(original_ledger)
        feed = fixture()
        feed["scores"] = {"ledger": original_ledger}
        feed["atmosphere"]["towns"]["langebaan"][0]["temperature_c"] = 19
        candidate_ledger = {}
        score_candidate(feed, candidate_ledger, NOW)
        entry = next(iter(candidate_ledger[VERSION]["records"].values()))
        self.assertEqual(entry["pairs"]["temp"], [19, 21, 17])
        self.assertEqual(entry["saved"], NOW.isoformat())
        self.assertEqual(original_ledger, original_copy)

    def test_all_methods_use_one_exact_station_measurement_and_freeze_actual(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        for bad in (report(station="wrong-station"), report(VALID + timedelta(minutes=1)),
                    {**report(), "distance_km": 16}):
            feed["stations"]["history"] = {"langebaan": [bad]}
            self.assertEqual(score_candidate(feed, ledger, VALID)["models"][VERSION]["n"], 0)
        feed["stations"]["history"] = {"langebaan": [report()]}
        s = score_candidate(feed, ledger, VALID)["models"][VERSION]
        self.assertEqual((s["n"], s["n_temp"], s["n_wind"]), (1, 1, 1))
        self.assertEqual([s[name + "_temp_mae_c"] for name in ("own", "baseline", "candidate")], [4, 5, 1])
        self.assertEqual([s[name + "_wind_mae_ms"] for name in ("own", "baseline", "candidate")], [5, 7, 1])
        feed["stations"]["history"]["langebaan"][0]["temperature_c"] = 999
        self.assertEqual(score_candidate(feed, ledger, VALID)["models"][VERSION], s)

    def test_equivalent_offset_timestamps_match_the_same_utc_instant(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        sast = VALID.astimezone(timezone(timedelta(hours=2)))
        feed["stations"]["history"] = {"langebaan": [report(sast)]}
        self.assertEqual(score_candidate(feed, ledger, VALID)["models"][VERSION]["n"], 1)

    def test_same_wigos_with_disagreeing_or_missing_coordinates_cannot_score_trial(self):
        for coords in ({"latitude": -33.1964, "longitude": 18.1241}, {"latitude": None}):
            feed, ledger = fixture(), {}
            score_candidate(feed, ledger, NOW)
            feed["stations"]["history"] = {"langebaan": [{**report(), **coords}]}
            self.assertEqual(score_candidate(feed, ledger, VALID)["models"][VERSION]["n"], 0)

    def test_no_retrospective_scores_or_filling_from_observations_already_due(self):
        feed = fixture()
        feed["stations"]["towns"] = {"langebaan": report()}
        feed["calibration"]["towns"]["langebaan"][0]["issued_utc"] = utc(VALID)
        ledger = {}
        result = score_candidate(feed, ledger, VALID)
        self.assertFalse(ledger[VERSION]["records"])
        self.assertEqual(result["models"][VERSION]["n"], 0)

    def test_training_cutoff_and_row_identity_are_enforced(self):
        for field, value in (("station_id", "wrong"), ("model_version", "other-model"),
                             ("issued_utc", utc(NOW - timedelta(hours=1)))):
            feed, ledger = fixture(), {}
            feed["calibration"]["towns"]["langebaan"][0][field] = value
            score_candidate(feed, ledger, NOW)
            self.assertFalse(ledger[VERSION]["records"], field)
        feed, ledger = fixture(), {}
        feed["calibration"]["training_cutoff_utc"] = utc(NOW)
        score_candidate(feed, ledger, NOW)
        self.assertFalse(ledger[VERSION]["records"])

    def test_expired_model_history_is_still_scored_without_new_forecasts(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        # Missing/expired calibration produces no rows; pending historical trials still mature.
        feed["calibration"] = {"status": "unavailable", "towns": {}}
        feed["stations"]["history"] = {"langebaan": [report()]}
        later = NOW + timedelta(days=8)
        s = score_candidate(feed, ledger, later)["models"][VERSION]
        self.assertEqual((s["n"], s["pending"]), (1, 0))
        self.assertEqual(len(ledger[VERSION]["records"]), 1)
        self.assertEqual(score_candidate(feed, ledger, NOW + timedelta(days=32))["models"][VERSION]["n"], 0)

    def test_reused_version_cannot_change_the_trial_target_or_training_cutoff(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        original = deepcopy(ledger)
        valid = VALID + timedelta(hours=1)
        for rows in (feed["atmosphere"]["towns"]["langebaan"],
                     feed["baseline"]["towns"]["langebaan"]["hours"],
                     feed["calibration"]["towns"]["langebaan"]):
            rows[0]["valid_utc"] = utc(valid)
        feed["calibration"]["target"]["station_id"] = "new-station"
        feed["calibration"]["towns"]["langebaan"][0]["station_id"] = "new-station"
        score_candidate(feed, ledger, NOW)
        self.assertEqual(ledger, original)

    def test_reused_version_cannot_change_training_period(self):
        feed, ledger = fixture(), {}
        score_candidate(feed, ledger, NOW)
        original = deepcopy(ledger)
        for rows in (feed["atmosphere"]["towns"]["langebaan"],
                     feed["baseline"]["towns"]["langebaan"]["hours"],
                     feed["calibration"]["towns"]["langebaan"]):
            rows[0]["valid_utc"] = utc(VALID + timedelta(hours=1))
        cutoff = utc(NOW - timedelta(hours=1))
        feed["calibration"]["training_cutoff_utc"] = cutoff
        feed["calibration"]["towns"]["langebaan"][0]["training_cutoff_utc"] = cutoff
        score_candidate(feed, ledger, NOW)
        self.assertEqual(ledger, original)


if __name__ == "__main__":
    unittest.main()
