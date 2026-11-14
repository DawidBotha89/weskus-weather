"""Narrow FM-12 decoder for the reviewed SAWS SMZA02 FAPR land bulletin.

Only section 1 temperature, dew point/RH and wind are used. Other SYNOP
sections, pressure, precipitation and unreviewed stations are never inferred.
"""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from point_sources import fetch, km, stamp

CACHE = "https://wis2globalcache.s3.us-east-1.amazonaws.com/"
BRIDGES = ("de-dwd-gts-to-wis2", "jp-jma-gts-to-wis2")
REGISTRY = json.loads(Path(__file__).with_name("station_registry.json").read_text(encoding="utf-8"))
SOURCE = "South African Weather Service via WMO WIS2 core (GTS bridge)"
METADATA = "https://oscar.wmo.int/surface/rest/api/wmd/download/0-20000-0-"


def report_time(day, hour, reference):
    # Resolve DDHH against delivery time across year/month boundaries. Never
    # accept an observation in the future or more than 48h before delivery.
    for days in range(3):
        candidate = (reference - timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate.day == day and reference - timedelta(hours=48) <= candidate <= reference:
            return candidate
    raise ValueError("SYNOP day/hour is inconsistent with its delivery timestamp")


def list_bulletins(bridge, now):
    prefix = f"data/{bridge}/data/core/S/M/Z/A/02/FAPR/"
    params = {"list-type": "2", "prefix": prefix}
    rows = []
    while True:
        data, _ = fetch(CACHE + "?" + urlencode(params), limit=2_000_000)
        root = ET.fromstring(data)
        for item in root.findall("{*}Contents"):
            key = item.findtext("{*}Key", "")
            modified = datetime.fromisoformat(item.findtext("{*}LastModified"))
            if key.startswith(prefix) and now - timedelta(hours=48) <= modified <= now:
                rows.append((key, item.findtext("{*}ETag"), stamp(modified)))
        token = root.findtext("{*}NextContinuationToken")
        if not token:
            return sorted(rows, key=lambda r: (r[2], r[0]))
        params["continuation-token"] = token


def signed_temperature(group):
    if not re.fullmatch(r"[12][01]\d{3}", group):
        return None
    value = int(group[2:]) / 10 * (-1 if group[1] == "1" else 1)
    return value if -93 <= value <= 66 else None


def parse_bulletin(data, reference):
    text = data.decode("ascii").replace("\x01", " ").replace("\x03", " ")
    header = re.search(r"\bSMZA02\s+FAPR\s+(\d{2})(\d{2})00(?:\s+(CC[A-Z]|RR[A-Z]))?", text)
    if not header:
        raise ValueError("Not the reviewed SAWS SMZA02 FAPR bulletin")
    when = report_time(int(header[1]), int(header[2]), reference)
    revision = header[3] or ""
    sections = re.split(r"\bAAXX\s+(\d{5})\s+", text[header.end():])
    if len(sections) < 3:
        raise ValueError("Missing FM-12 AAXX header")
    rows = []
    for pos in range(1, len(sections), 2):
        code, body = sections[pos:pos+2]
        if report_time(int(code[:2]), int(code[2:4]), reference) != when:
            raise ValueError("Bulletin and AAXX observation times disagree")
        factor = {"0": 1, "1": 1, "3": 1852/3600, "4": 1852/3600}.get(code[4])
        for raw in body.split("="):
            parts = raw.split()
            if not parts or parts[0] not in REGISTRY["stations"] or "NIL" in parts:
                continue
            if len(parts) < 3 or not all(re.fullmatch(r"[0-9/]{5}", p) for p in parts[:3]):
                raise ValueError("Malformed reviewed station's SYNOP section 1")
            groups = []
            for group in parts[3:]:
                if group in ("333", "444", "555") or group.startswith("222"):
                    break
                groups.append(group)
            temp = next((signed_temperature(g) for g in groups if g.startswith("1")), None)
            dewgroup = next((g for g in groups if g.startswith("2")), "")
            dew = signed_temperature(dewgroup)
            rh = int(dewgroup[2:]) if re.fullmatch(r"29\d{3}", dewgroup) and int(dewgroup[2:]) <= 100 else None
            windcode = parts[2]
            speed = int(windcode[3:]) if windcode[3:].isdigit() else None
            if speed == 99:
                extended = next((g for g in groups if re.fullmatch(r"00\d{3}", g)), None)
                speed = int(extended[2:]) if extended else None
            speed = round(speed * factor, 4) if speed is not None and factor is not None else None
            if speed is not None and not 0 <= speed <= 115:
                speed = None
            direction = int(windcode[1:3]) if windcode[1:3].isdigit() else None
            direction = (direction * 10) % 360 if direction is not None and 1 <= direction <= 36 and speed else None
            if all(v is None for v in (temp, rh, speed)):
                continue
            rows.append({"id": parts[0], "observed_utc": stamp(when), "temperature_c": temp,
                         "dewpoint_c": dew, "reported_rh": rh, "wind_speed_ms": speed,
                         "wind_from_deg": direction, "source_revision": revision})
    return rows


def preference(row):
    # Corrected reports win over delayed/routine duplicates, including next run.
    rev = row.get("source_revision", "")
    return (rev if rev.startswith("CC") else "", row.get("available_utc", ""))


def object_period(key, modified):
    """Identify the failed bulletin period without pretending it was decoded."""
    match = re.search(r"SMZA02FAPR(\d{2})(\d{2})00(CC[A-Z]|RR[A-Z])?_", key)
    if not match:
        return None, ""
    try:
        when = stamp(report_time(int(match[1]), int(match[2]), datetime.fromisoformat(modified)))
    except ValueError:
        return None, ""
    revision = match[3] or ""
    return when, revision if revision.startswith("CC") else ""


def collect_bulletins(previous, now, stations, centres, humidity):
    old_cache = (previous or {}).get("bulletin_cache", {})
    retained_sources = {(r.get("source_url"), r.get("source_etag"))
                        for records in (previous or {}).get("history", {}).values() for r in records}
    retained_sources.update((r.get("source_url"), r.get("source_etag"))
                            for r in (previous or {}).get("towns", {}).values())
    failures = []
    failed_reports, recovered_periods = [], {}
    partial_rows, partial_cache = [], {}

    def unresolved():
        return [message for period, revision, message in failed_reports
                if period is None or period not in recovered_periods or recovered_periods[period] < revision]

    for bridge in BRIDGES:
        rows, cache, errors = [], {}, []
        try:
            items = list_bulletins(bridge, now)
            if not items:
                raise ValueError("No recent SAWS SYNOP bulletins in bridge index")
        except (OSError, ValueError, ET.ParseError) as err:
            failures.append(f"{bridge}: {err}")
            continue
        for key, etag, modified in items:
            url = CACHE + key
            period, revision = object_period(key, modified)
            if old_cache.get(url) == etag and (url, etag) in retained_sources:
                cache[url] = etag
                if period:
                    recovered_periods[period] = max(recovered_periods.get(period, ""), revision)
                continue
            try:
                data, _ = fetch(url, limit=128_000, headers={"If-Match": etag})
                decoded = parse_bulletin(data, datetime.fromisoformat(modified))
                if period and any(row["observed_utc"] != period for row in decoded):
                    raise ValueError("SYNOP object key and decoded observation time disagree")
                for item in decoded:
                    for slug, (station_id, _, limit) in stations.items():
                        if station_id != "0-20000-0-" + item["id"]:
                            continue
                        meta = REGISTRY["stations"][item["id"]]
                        distance = km(centres[slug], meta["latitude"], meta["longitude"])
                        if distance > limit:
                            raise ValueError("OSCAR station exceeds reviewed town proxy distance")
                        t, d = item["temperature_c"], item["dewpoint_c"]
                        rh, method = humidity(item["reported_rh"], None if t is None else t+273.15,
                                              None if d is None else d+273.15)
                        row = {k: v for k, v in item.items() if k not in ("id", "reported_rh", "dewpoint_c")}
                        row.update(station_id=station_id, station_name=meta["name"],
                                   latitude=meta["latitude"], longitude=meta["longitude"], distance_km=distance,
                                   coordinate_metadata_url=METADATA+item["id"], exposure=meta["exposure"],
                                   coordinate_source="OSCAR registry metadata; position is not encoded in FM-12",
                                   humidity_percent=rh, humidity_method=method, available_utc=modified,
                                   received_utc=stamp(now), source_url=url, source_etag=etag,
                                   source_name=SOURCE, source_format="FM-12 SYNOP")
                        rows.append((slug, row))
                cache[url] = etag
                if period:
                    recovered_periods[period] = max(recovered_periods.get(period, ""), revision)
            except (OSError, ValueError, UnicodeError) as err:
                message = f"{key}: {err}"
                errors.append(message)
                failed_reports.append((period, revision, message))
        # An empty/NIL bulletin is legitimate; a failed download is not. Try the
        # independent bridge when the primary has access or decoding failures.
        if not errors:
            # A healthy older mirror must not erase valid newer primary rows.
            # Clear an item failure only when its period and correction level
            # were recovered; a merely nonempty fallback is not completeness.
            return partial_rows + rows, {**partial_cache, **cache}, unresolved()
        partial_rows.extend(rows)
        partial_cache.update(cache)
    return partial_rows, partial_cache, failures + unresolved()


def coverage(centres, station_map, observations):
    result = {}
    for slug, centre in centres.items():
        if slug not in station_map:
            result[slug] = {"status": "unavailable", "reason": REGISTRY["unavailable"].get(slug, "No reviewed nearby public observation.")}
            continue
        station_id, _, limit = station_map[slug]
        ident = station_id.rsplit("-", 1)[-1]
        meta = REGISTRY["stations"][ident]
        row = observations.get(slug)
        result[slug] = {"status": "report_available" if row else "awaiting_report", "station_id": station_id,
                        "station_name": meta["name"], "latitude": meta["latitude"], "longitude": meta["longitude"],
                        "distance_km": km(centre, meta["latitude"], meta["longitude"]), "max_distance_km": limit,
                        "coordinate_metadata_url": METADATA+ident, "exposure": meta["exposure"],
                        "coordinate_source": "OSCAR registry metadata; reported BUFR positions may differ",
                        "observed_utc": row.get("observed_utc") if row else None}
    return result
