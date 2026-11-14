"""Pinned retrospective temporal test and a separately frozen future Geelbek candidate."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from statistics import mean

from calibration import STATION, calibrated_candidate, corrected, fit_model, instant, period, utc


TRAIN_CUTOFF = "2026-11-13T06:00:00Z"


def matched_samples(ledger, history):
    observed = {}
    for snapshot in sorted(history, key=lambda s: instant(s["generated"])):
        stations = snapshot["stations"]
        records = list(stations.get("history", {}).get("langebaan", ()))
        latest = stations.get("towns", {}).get("langebaan")
        if latest:
            records.append(latest)
        for row in records:
            if row["station_id"] == STATION and row["distance_km"] == 12.71:
                observed.setdefault(instant(row["observed_utc"]), (row, snapshot["generated"]))
    samples, excluded = [], []
    for time, pair in sorted(ledger["langebaan"].items()):
        if not pair.get("actual"):
            continue
        observation = observed.get(instant(time))
        if observation is None:
            excluded.append({"valid_utc": time, "reason": "No archived observation receipt evidence"})
            continue
        row, archived = observation
        actual = pair["actual"]
        if (pair["station"] != STATION or abs(actual["temp"] - row["temperature_c"]) > 1e-6
                or abs(actual["wind"] - row["wind_speed_ms"]) > 1e-6):
            raise ValueError("Ledger measurement does not match the archived station report")
        samples.append({"station_id": STATION, "valid_utc": utc(instant(time)), "saved_utc": pair["saved"],
                        "available_utc": row["available_utc"], "archived_utc": archived,
                        "temperature_c": pair["pairs"]["temp"][0], "wind_speed_ms": pair["pairs"]["wind"][0],
                        "met_temperature_c": pair["pairs"]["temp"][1], "met_wind_speed_ms": pair["pairs"]["wind"][1],
                        "actual_temperature_c": actual["temp"], "actual_wind_speed_ms": actual["wind"]})
    return samples, excluded


def score(samples, model):
    pairs = []
    for row in samples:
        temp, wind = corrected(row["temperature_c"], row["wind_speed_ms"], model["parameters"][period(row["valid_utc"])])
        pairs.append({**row, "candidate_temperature_c": temp, "candidate_wind_speed_ms": wind})
    metrics = {}
    for prefix, title in (("", "raw"), ("met_", "met"), ("candidate_", "candidate")):
        metrics[title] = {}
        for field in ("temperature_c", "wind_speed_ms"):
            errors = [row[prefix + field] - row["actual_" + field] for row in pairs]
            metrics[title][field] = {"mae": round(mean(map(abs, errors)), 4), "bias": round(mean(errors), 4)} if errors else None
    return {"n": len(pairs), "day_n": sum(period(s["valid_utc"]) == "day" for s in pairs),
            "night_n": sum(period(s["valid_utc"]) == "night" for s in pairs), "metrics": metrics, "pairs": pairs}


def run(evidence, output):
    inputs = {name: (evidence / name).read_bytes() for name in ("ledger.json", "history.json", "matched-rows.json", "latest.json")}
    samples, excluded = matched_samples(json.loads(inputs["ledger.json"]), json.loads(inputs["history.json"]))
    # Parameters and the cutoff were fixed before this script first scored the later observations.
    model = fit_model(samples, TRAIN_CUTOFF, TRAIN_CUTOFF)
    later = [s for s in samples if instant(s["valid_utc"]) >= instant(TRAIN_CUTOFF)]
    issued_later = [s for s in later if instant(s["saved_utc"]) >= instant(TRAIN_CUTOFF)]
    snapshot = json.loads(inputs["latest.json"])
    future_cutoff = snapshot["generated_utc"]
    future = fit_model(samples, future_cutoff, utc(datetime.now(UTC)))
    report = {"method": "Retrospective temporal replay; later audit patterns informed the choice of model family.",
              "prospectively_validated": False,
              "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()},
              "excluded": excluded, "early_model": model,
              "later_observation_holdout": score(later, model),
              "saved_after_training_cutoff": score(issued_later, model),
              "limits": ["Repeated observations cover very few days and share weather conditions.",
                         "The fitted location is Geelbek, 12.71 km from Langebaan.",
                         "The strict issue-time replay has no independent nighttime observations.",
                         "The future candidate uses later observations too and requires new frozen prospective scores."]}
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("evaluation.json", report), ("geelbek-model.json", future),
                       ("candidate.json", calibrated_candidate(snapshot["atmosphere"], future))):
        (output / name).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: {a: b for a, b in report[k].items() if a != "pairs"}
                      for k in ("later_observation_holdout", "saved_after_training_cutoff")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run(args.evidence, args.out)
