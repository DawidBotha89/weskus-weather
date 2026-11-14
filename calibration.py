"""Experimental Geelbek station proxy correction of the Langebaan IFS series."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from statistics import median


TOWN = "langebaan"
STATION = "0-20000-0-68811"
METHOD = "geelbek-two-period-median-v1"
MAX_TEMP_OFFSET_C = 4
WIND_FACTOR_LIMITS = (.5, 1.25)
SUPPORTED_UTC_HOURS = (3, 9, 15, 21)
TARGET = {"town": TOWN, "station_id": STATION, "station_name": "Geelbek AWS",
          "latitude": -33.18334, "longitude": 18.11666, "distance_km": 12.71,
          "scope": "Geelbek station proxy using the Langebaan town forecast"}


def instant(value):
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if time.tzinfo is None:
        raise ValueError("Calibration times need an explicit timezone")
    return time.astimezone(UTC)


def utc(time):
    return time.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def period(valid):
    hour = (instant(valid) + timedelta(hours=2)).hour
    return "day" if 6 <= hour < 18 else "night"


def corrected(temp, wind, parameters):
    if not math.isfinite(temp) or not math.isfinite(wind) or wind < 0:
        raise ValueError("Invalid forecast temperature or wind speed")
    offset, factor = parameters["temperature_offset_c"], parameters["wind_factor"]
    if not abs(offset) <= MAX_TEMP_OFFSET_C or not WIND_FACTOR_LIMITS[0] <= factor <= WIND_FACTOR_LIMITS[1]:
        raise ValueError("Calibration coefficients exceed the fixed bounds")
    return round(temp + offset, 3), round(wind * factor, 3)


def fit_model(samples, cutoff_utc, created_utc):
    """Use only measurements demonstrably received and archived before the cutoff."""
    cutoff, created = instant(cutoff_utc), instant(created_utc)
    if created < cutoff:
        raise ValueError("Model creation precedes training cutoff")
    eligible = []
    for sample in samples:
        if sample["station_id"] != STATION:
            raise ValueError("Unexpected calibration target")
        valid, saved = instant(sample["valid_utc"]), instant(sample["saved_utc"])
        if saved >= valid:
            raise ValueError("Forecast was saved after its observation")
        if (valid >= cutoff or instant(sample["available_utc"]) >= cutoff
                or instant(sample["archived_utc"]) >= cutoff):
            continue
        values = [sample[k] for k in ("temperature_c", "wind_speed_ms", "actual_temperature_c", "actual_wind_speed_ms")]
        if not all(math.isfinite(v) for v in values) or min(values[1], values[3]) < 0:
            raise ValueError("Missing or invalid measured weather")
        eligible.append(sample)
    eligible.sort(key=lambda row: instant(row["valid_utc"]))
    if len({instant(s["valid_utc"]) for s in eligible}) != len(eligible):
        raise ValueError("Duplicate calibration observation times")
    params = {}
    for name in ("day", "night"):
        rows = [s for s in eligible if period(s["valid_utc"]) == name]
        ratios = [s["actual_wind_speed_ms"] / s["wind_speed_ms"] for s in rows if s["wind_speed_ms"] >= 1]
        if len(rows) < 2 or len(ratios) < 2:
            raise ValueError("Need two measured pairs and two non-calm model winds per period")
        offset = median(s["actual_temperature_c"] - s["temperature_c"] for s in rows)
        factor = median(ratios)
        params[name] = {"temperature_offset_c": round(max(-4, min(4, offset)), 5),
                        "wind_factor": round(max(.5, min(1.25, factor)), 5), "training_n": len(rows)}
    fingerprint = hashlib.sha256(json.dumps(eligible, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"method": METHOD, "model_version": METHOD + "-" + fingerprint[:12],
            "status": "experimental station proxy", "validated_default": False, "target": dict(TARGET),
            "created_utc": utc(created), "training_cutoff_utc": utc(cutoff),
            "expires_utc": utc(cutoff + timedelta(days=7)), "training_n": len(eligible),
            "training_from_utc": eligible[0]["valid_utc"], "training_until_utc": eligible[-1]["valid_utc"],
            "training_sha256": fingerprint, "parameters": params,
            "training_lead_hours": [round(min((instant(s["valid_utc"])-instant(s["saved_utc"])).total_seconds()/3600 for s in eligible), 3),
                                    round(max((instant(s["valid_utc"])-instant(s["saved_utc"])).total_seconds()/3600 for s in eligible), 3)],
            "uncertainty": {"prediction_interval": None,
                            "reason": "Too few independent weather days to estimate reliable coverage."}}


def calibrated_candidate(atmosphere, model, now=None):
    """Return a separately labelled candidate; never change the input forecast."""
    now = now or datetime.now(UTC)
    result = {"status": "unavailable", "target": dict(TARGET), "towns": {},
              "supported_sast_hours": [5, 11, 17, 23]}
    if not model or model.get("method") != METHOD or model.get("target") != TARGET:
        return result
    result.update({k: model[k] for k in ("model_version", "training_cutoff_utc", "expires_utc", "training_n", "uncertainty")})
    if not instant(model["created_utc"]) <= now < instant(model["expires_utc"]):
        result["reason"] = "Model unavailable at this issue time or expired"
        return result
    run = instant(atmosphere["run_utc"])
    if not now - timedelta(hours=24) <= run <= now:
        result["reason"] = "Source run outside the allowed age"
        return result
    rows = []
    for row in atmosphere.get("towns", {}).get(TOWN, ()):
        valid = instant(row["valid_utc"])
        # The limited training archive supports four exact times of day.
        # Keep the unobserved dawn/dusk transition hours outside this pilot.
        if valid.hour not in SUPPORTED_UTC_HOURS or valid.minute or valid.second or valid.microsecond:
            continue
        # Scores target approximately day-ahead forecasts. Do not extrapolate to a week.
        if not now < valid <= min(now + timedelta(hours=24), instant(model["expires_utc"])):
            continue
        temp, wind = corrected(row["temperature_c"], row["wind_speed_ms"], model["parameters"][period(row["valid_utc"])])
        rows.append({"valid_utc": utc(valid), "issued_utc": utc(now), "source_run_utc": atmosphere["run_utc"],
                     "temperature_c": temp, "wind_speed_ms": wind, "station_id": STATION,
                     "model_version": model["model_version"], "training_cutoff_utc": model["training_cutoff_utc"]})
    result.update(status="experimental station proxy", towns={TOWN: rows})
    return result
