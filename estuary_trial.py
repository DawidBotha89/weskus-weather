"""A separately labelled water-level experiment; never emits estuary tide times."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import math
from pathlib import Path
from statistics import mean
import subprocess
import time

import estuary_observations as observations
from point_sources import stamp

ROOT = Path(__file__).parent
GRID_SECONDS = 300


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Explicit UTC offset required")
    return parsed.astimezone(UTC)


def boundary(times, model):
    blob = (ROOT / "data/tide-stations.json").read_bytes()
    if hashlib.sha256(blob).hexdigest() != model["boundary_sha256"]:
        raise ValueError("Frozen boundary constants changed; model needs review")
    helper = ROOT / "coastal_boundary.cjs"
    if hashlib.sha256(helper.read_bytes()).hexdigest() != model["boundary_implementation_sha256"]:
        raise ValueError("Frozen boundary implementation changed; model needs review")
    installed = json.loads((ROOT / "node_modules/@neaps/tide-predictor/package.json").read_text(encoding="utf-8"))
    if "@neaps/tide-predictor@" + installed["version"] != model["boundary_predictor_version"]:
        raise ValueError("Frozen predictor version changed; model needs review")
    request = {"latitude": model["latitude"], "times": times, "minutes": [0, model["lag_minutes"]]}
    result = subprocess.run(["node", str(ROOT / "coastal_boundary.cjs")], input=json.dumps(request),
                            capture_output=True, text=True, check=True, timeout=45)
    rows = json.loads(result.stdout)["levels"]
    if len(rows) != len(times) or any(len(row) != 2 or not all(math.isfinite(x) for x in row) for row in rows):
        raise ValueError("Incomplete boundary prediction")
    return rows


def freeze(ledger, model, now):
    version = model["model_version"]
    trial = ledger.setdefault(version, {"model": deepcopy(model), "records": {}, "matches": {}})
    if trial["model"] != model:
        raise ValueError("A frozen estuary model version cannot change")
    if not instant(model["created_utc"]) <= now < instant(model["expires_utc"]):
        return
    start = math.ceil((now + timedelta(hours=6)).timestamp()/GRID_SECONDS)*GRID_SECONDS
    end = min((now+timedelta(hours=30)).timestamp(), instant(model["expires_utc"]).timestamp())
    times = [stamp(datetime.fromtimestamp(t, UTC)) for t in range(start, int(end)+1, GRID_SECONDS)]
    missing = [t for t in times if t not in trial["records"]]
    if not missing:
        return
    for valid, (coast, lagged) in zip(missing, boundary(missing, model), strict=True):
        trial["records"][valid] = {"issued_utc": stamp(now), "values": [
            round(model["offset_m"]+model["gain"]*lagged, 6),
            round(coast+model["coast_offset_m"], 6), model["training_mean_m"]]}


def matched_prediction(records, observed):
    seconds = observed.timestamp()
    lower = math.floor(seconds/GRID_SECONDS)*GRID_SECONDS
    row = records.get(stamp(datetime.fromtimestamp(lower, UTC)))
    if row is None or not timedelta(hours=6) <= observed-instant(row["issued_utc"]) <= timedelta(hours=30):
        return None
    # Native Neaps floors the baseline to ten-minute bins; the frozen 55-minute
    # lag adds boundaries halfway between them. A five-minute LEFT-bin lookup
    # exactly preserves that fitted model. Linear interpolation would change it.
    return list(row["values"])


def scores(ledger, source, now):
    summaries = {}
    for version, trial in ledger.items():
        model, records, matches = trial["model"], trial["records"], trial["matches"]
        for row in source.get("history", []):
            observed = instant(row["observed_utc"])
            key = stamp(observed)
            existing = matches.get(key)
            identity = ("level_m", "quality", "quality_bits", "datum", "collection", "reach_id", "latitude", "longitude")
            if existing and any(existing["observed"].get(field) != row.get(field) for field in identity):
                existing["excluded_reason"] = "source_revised_after_first_match"
            if (row.get("reach_id") != model["reach_id"] or row.get("datum") != model["datum"]
                    or row.get("collection") != model["collection"] or row.get("quality") not in (0, 1)
                    or not math.isfinite(row["level_m"]) or observed > now
                    or abs(row["latitude"]-model["latitude"]) > .0001
                    or abs(row["longitude"]-model["longitude"]) > .0001):
                continue
            predicted = matched_prediction(records, observed)
            if predicted is not None and key not in matches:
                matches[key] = {"predicted": predicted, "observed": deepcopy(row),
                                "scored_utc": stamp(now), "native_grid_seconds": GRID_SECONDS}
        for key in list(records):
            if now-instant(key) > timedelta(days=180):
                del records[key]
        summary = {"pending_grid_points": sum(instant(key)>now for key in records),
                   "excluded_revisions": sum(bool(row.get("excluded_reason")) for row in matches.values()), "groups": {}}
        # A suspect satellite height must not become independent high-quality proof.
        for quality, label in ((0, "good_only"), (1, "suspect_only")):
            rows = [row for row in matches.values() if row["observed"]["quality"] == quality and not row.get("excluded_reason")]
            group = {"n": len(rows)}
            for index, method in enumerate(("candidate", "coast_aligned", "training_mean")):
                errors = [r["predicted"][index]-r["observed"]["level_m"] for r in rows]
                group[method] = {"mae_m": round(mean(map(abs, errors)), 4) if errors else None,
                                 "rmse_m": round(math.sqrt(mean(e*e for e in errors)), 4) if errors else None}
            summary["groups"][label] = group
        summaries[version] = summary
    return summaries


def update(previous, ledger, now=None):
    now = now or datetime.now(UTC)
    model = json.loads((ROOT / "research/berg-model.json").read_text(encoding="utf-8"))
    source = deepcopy(previous.get("berg", {}).get("observations", {}))
    source_error = None
    attempts = 0
    for attempt in range(1, 4):
        attempts = attempt
        if attempt > 1:
            time.sleep((20, 60)[attempt-2])
        try:
            source = observations.collect(source, now)
            source_error = None
            break
        except Exception as error:
            logging.warning("Estuary observations attempt %s failed (%s)", attempt, type(error).__name__)
            source_error = "observation_refresh_failed"
    freeze(ledger, model, now)
    summary = scores(ledger, source, now)
    trial = ledger[model["model_version"]]
    hours = [{"valid_utc": key, "candidate_m": row["values"][0], "coast_aligned_m": row["values"][1]}
             for key, row in sorted(trial["records"].items())
             if now < instant(model["expires_utc"]) and now < instant(key) <= now+timedelta(hours=30) and instant(key).minute == 0]
    latest = source.get("latest")
    return {"updated_utc": stamp(now), "status": "research only", "default_changed": False,
            "health": {"status": "degraded" if source_error else "complete", "error": source_error,
                       "attempts": attempts, "last_success_utc": previous.get("health", {}).get("last_success_utc") if source_error else stamp(now)},
            "berg": {"model_version": model["model_version"], "name": "Berg River satellite reach near Velddrif",
                     "reach_id": model["reach_id"], "latitude": model["latitude"], "longitude": model["longitude"],
                     "datum": model["datum"], "datum_note": "Statistical alignment to satellite heights; not chart datum or a surveyed gauge zero",
                     "expires_utc": model["expires_utc"], "training_until_utc": model["training_until_utc"],
                     "observations": source, "latest_observation": latest, "hours": hours,
                     "scores": summary[model["model_version"]],
                     "limits": "Sparse, quality-flagged reach averages cannot validate high/low tide times, currents or navigation."},
            "langebaan": {"status": "awaiting_local_gauge_data", "candidate_available": False,
                          "gauge_source": "Saldanha Bay Water Quality Forum Trust lagoon logger",
                          "reason": "No verified reusable continuous lagoon level series yet; seven historical laser passes are insufficient for the predefined holdout.",
                          "coastal_default_retained": True}}
