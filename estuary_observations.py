"""Version-pinned NASA observations for a named Berg reach, never a live tide gauge."""
import csv
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import math
from urllib.parse import urlencode

from point_sources import fetch, stamp

REACH = "12602000013"
COLLECTION = "SWOT_L2_HR_RiverSP_D"
DATUM = "EGM2008 geoid"
POSITION = (-32.79005, 18.16711)
API = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"
FIELDS = "reach_id,river_name,p_lat,p_lon,time_str,wse,wse_u,reach_q,reach_q_b,cycle_id,pass_id"


def decode(blob, url, now):
    payload = json.loads(blob)
    if payload.get("status") != "200 OK" or not isinstance(payload.get("results", {}).get("csv"), str):
        raise ValueError("Unexpected Hydrocron response")
    reader = csv.DictReader(io.StringIO(payload["results"]["csv"]))
    if not set(FIELDS.split(",") + ["wse_units", "wse_u_units", "p_lat_units", "p_lon_units"]) <= set(reader.fieldnames or []):
        raise ValueError("Missing satellite observation fields")
    reports = {}
    for row in reader:
        if row["reach_id"] != REACH or row["wse_units"] != "m" or row["wse_u_units"] != "m":
            raise ValueError("Unexpected reach identity or units")
        if row["p_lat_units"] != "degrees_north" or row["p_lon_units"] != "degrees_east":
            raise ValueError("Unexpected coordinate units")
        lat, lon = float(row["p_lat"]), float(row["p_lon"])
        if not all(math.isfinite(v) for v in (lat, lon)) or abs(lat-POSITION[0]) > .0001 or abs(lon-POSITION[1]) > .0001:
            raise ValueError("Satellite reach reference position changed")
        observed = datetime.fromisoformat(row["time_str"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > now:
            raise ValueError("Invalid satellite observation time")
        level, uncertainty = float(row["wse"]), float(row["wse_u"])
        quality = int(row["reach_q"])
        if quality not in (0, 1, 2, 3):
            raise ValueError("Unknown SWOT quality code")
        if not all(math.isfinite(v) for v in (level, uncertainty)) or abs(level) > 20 or not 0 < uncertainty < 1:
            continue  # Known fill/missing values are not measurements.
        key = stamp(observed)
        report = {"observed_utc": key, "first_seen_utc": stamp(now), "level_m": level,
                  "uncertainty_m": uncertainty, "quality": quality,
                  "quality_label": ("good", "suspect", "degraded", "bad")[quality],
                  "quality_bits": row["reach_q_b"], "cycle": row["cycle_id"], "pass": row["pass_id"],
                  "reach_id": REACH, "latitude": lat, "longitude": lon, "datum": DATUM,
                  "collection": COLLECTION, "source_url": url,
                  "response_sha256": hashlib.sha256(blob).hexdigest()}
        if key in reports:
            raise ValueError("Duplicate satellite observation time needs review")
        reports[key] = report
    return list(reports.values())


def collect(previous, now=None):
    now = now or datetime.now(UTC)
    if previous.get("collection") == COLLECTION and previous.get("fetched_utc"):
        age = now - datetime.fromisoformat(previous["fetched_utc"])
        if timedelta(0) <= age < timedelta(hours=6):
            return previous
    params = {"feature": "Reach", "feature_id": REACH,
              "start_time": stamp(now-timedelta(days=180)), "end_time": stamp(now),
              "collection_name": COLLECTION, "output": "csv", "fields": FIELDS}
    url = API + "?" + urlencode(params)
    blob, _ = fetch(url, limit=2_000_000, headers={"Accept": "application/json"})
    incoming = decode(blob, url, now)
    cutoff = stamp(now-timedelta(days=180))
    old = {r["observed_utc"]: r for r in previous.get("history", [])
           if r.get("collection") == COLLECTION and r["observed_utc"] >= cutoff}
    for row in incoming:
        prior = old.get(row["observed_utc"])
        if prior:
            row["first_seen_utc"] = prior["first_seen_utc"]
        old[row["observed_utc"]] = row
    history = [old[k] for k in sorted(old)]
    usable = [row for row in history if row["quality"] <= 1]
    return {"collection": COLLECTION, "fetched_utc": stamp(now), "history": history,
            "latest": usable[-1] if usable else None, "reach_id": REACH, "datum": DATUM,
            "source": "NASA/CNES SWOT RiverSP Version D via PO.DAAC Hydrocron",
            "source_url": url, "licence_url": "https://science.data.nasa.gov/about/license",
            "dataset_url": "https://doi.org/10.5067/SWOT-RIVERSP-D",
            "cadence": "Sparse satellite overpasses; checked every six hours, not a real-time gauge"}
