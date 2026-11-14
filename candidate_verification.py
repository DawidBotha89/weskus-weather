"""Prospective three-way scores. Never backfill a newly fitted model into old forecasts."""
from datetime import UTC, datetime, timedelta
from copy import deepcopy
from statistics import mean
from math import asin, cos, radians, sin, sqrt

from verification import collect_pairs, VARIABLES


def same_location(report, target):
    # WIGOS identity alone is insufficient when bulletin metadata and reported
    # sensor coordinates disagree. Keep this trial tied to its training location.
    try:
        lat, lon = report["latitude"], report["longitude"]
        a, b = radians(lat), radians(target["latitude"])
        hav = sin((a-b)/2)**2 + cos(a)*cos(b)*sin(radians(lon-target["longitude"])/2)**2
        return 6371 * 2 * asin(sqrt(min(1, hav))) <= 1
    except (KeyError, TypeError, ValueError):
        return False


def score_candidate(feed, ledger, now):
    candidate = feed.get("calibration", {})
    version = candidate.get("model_version")
    target = candidate.get("target", {})
    town, station_id = target.get("town"), target.get("station_id")
    if version and town and station_id:
        trial = ledger.setdefault(version, {
            "target": deepcopy(target), "training_cutoff_utc": candidate["training_cutoff_utc"], "records": {}})
        same_model = trial["target"] == target and trial["training_cutoff_utc"] == candidate["training_cutoff_utc"]
        records = trial["records"]
        # Generate a fresh set: the original two-way ledger may have been saved before fitting.
        fresh = {}
        collect_pairs(feed, fresh, now)
        for row in candidate.get("towns", {}).get(town, []) if same_model else []:
            valid = datetime.fromisoformat(row["valid_utc"]).astimezone(UTC)
            key = valid.isoformat()
            pair = fresh.get(town, {}).get(key)
            if (not pair or key in records or row.get("model_version") != version
                    or row.get("station_id") != station_id
                    or datetime.fromisoformat(row["issued_utc"]) != now.replace(microsecond=0)
                    or datetime.fromisoformat(candidate["training_cutoff_utc"]) >= now):
                continue
            triples = {name: pair["pairs"][name] + [row[field]] for name, field in VARIABLES.items()
                       if name in pair["pairs"] and row.get(field) is not None}
            if triples:
                records[key] = {**pair, "pairs": triples, "model_version": version}

    summaries = {}
    for model_version, trial in ledger.items():
        target, records = trial["target"], trial["records"]
        town = target["town"]
        reports = list(feed["stations"].get("history", {}).get(town, []))
        reports.append(feed["stations"].get("towns", {}).get(town) or {})
        for report in reports:
            if (report.get("station_id") != target["station_id"] or not report.get("observed_utc")
                    or not same_location(report, target)
                    or not 0 <= report.get("distance_km", float("inf")) <= 15):
                continue
            observed = datetime.fromisoformat(report["observed_utc"]).astimezone(UTC)
            entry = records.get(observed.isoformat())
            if entry and datetime.fromisoformat(entry["saved"]) < observed <= now:
                actual = entry.setdefault("actual", {})
                for name, field in VARIABLES.items():
                    if name in entry["pairs"] and report.get(field) is not None:
                        actual.setdefault(name, report[field])
                entry.setdefault("observation_source", report.get("source_url"))
        for key in list(records):
            if now - datetime.fromisoformat(key) > timedelta(days=30):
                del records[key]
        summary = {"n": sum(bool(r.get("actual")) for r in records.values()),
                   "pending": sum(datetime.fromisoformat(k) > now for k in records),
                   "target": target, "training_cutoff_utc": trial["training_cutoff_utc"]}
        for variable, unit in (("temp", "c"), ("wind", "ms")):
            rows = [r for r in records.values() if variable in r.get("actual", {})]
            summary["n_" + variable] = len(rows)
            for i, name in enumerate(("own", "baseline", "candidate")):
                summary[f"{name}_{variable}_mae_{unit}"] = round(mean(
                    abs(r["pairs"][variable][i] - r["actual"][variable]) for r in rows), 3) if rows else None
        summaries[model_version] = summary
    return {"models": summaries, "window_days": 30, "lead_hours": [6, 24], "prospective_only": True}
