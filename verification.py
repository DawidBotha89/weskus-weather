"""Freeze comparable pairs before validity; verify only against a nearby, time-matched instrument."""
from datetime import datetime, timedelta
from statistics import mean

VARIABLES = {"temp": "temperature_c", "wind": "wind_speed_ms"}


def collect_pairs(feed, ledger, now):
    air = feed["atmosphere"]
    for town, own in air.get("towns", {}).items():
        baseline = feed["baseline"].get("towns", {}).get(town, {})
        if not baseline or baseline.get("stale"):
            continue
        # Reject stale baseline caches even if some forecast times are still in the future.
        if now - datetime.fromisoformat(baseline["fetched_utc"]) > timedelta(hours=2):
            continue
        if now - datetime.fromisoformat(air["run_utc"]) > timedelta(hours=24):
            continue
        others = {datetime.fromisoformat(x["valid_utc"]): x for x in baseline["hours"]}
        for row in own:
            valid = datetime.fromisoformat(row["valid_utc"])
            if valid not in others or not timedelta(hours=6) <= valid - now <= timedelta(hours=24):
                continue
            # First shared forecast seen in the window wins, even across retries or newer runs.
            entries = ledger.setdefault(town, {})
            key = valid.isoformat()
            if key in entries:
                continue
            pairs = {name: [row[field], others[valid][field]] for name, field in VARIABLES.items()
                     if row.get(field) is not None and others[valid].get(field) is not None}
            if pairs:
                entries[key] = {"saved": now.isoformat(), "model_run": air["run_utc"],
                                "baseline_update": baseline["updated_utc"], "pairs": pairs}


def score(feed, ledger, now):
    collect_pairs(feed, ledger, now)
    towns = {}
    for town, records in ledger.items():
        reports = list(feed["stations"].get("history", {}).get(town, []))
        reports.append(feed["stations"].get("towns", {}).get(town) or {})
        for station in reports:
            if not station.get("observed_utc") or not 0 <= station.get("distance_km", float("inf")) <= 15:
                continue
            observed = datetime.fromisoformat(station["observed_utc"])
            entry = records.get(observed.isoformat())
            if entry and observed <= now and datetime.fromisoformat(entry["saved"]) < observed:
                entry.setdefault("station", station["station_id"])
                if entry["station"] == station["station_id"]:
                    actual = entry.setdefault("actual", {})
                    for name, field in VARIABLES.items():
                        if name in entry["pairs"] and station.get(field) is not None:
                            actual.setdefault(name, station[field])
        for key in list(records):
            if now - datetime.fromisoformat(key) > timedelta(days=30):
                del records[key]
        matched = [r for r in records.values() if r.get("actual")]
        summary = {"n": len(matched)}
        for variable, unit in (("temp", "c"), ("wind", "ms")):
            rows = [r for r in matched if variable in r["actual"]]
            summary["n_" + variable] = len(rows)
            for i, name in enumerate(("own", "baseline")):
                summary[f"{name}_{variable}_mae_{unit}"] = round(mean(
                    abs(r["pairs"][variable][i] - r["actual"][variable]) for r in rows), 2) if rows else None
        towns[town] = summary
    return {"towns": towns, "window_days": 30, "lead_hours": [6, 24], "max_station_distance_km": 15}
