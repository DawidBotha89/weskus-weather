"""Latest nearby South African Weather Service observations from WMO WIS2 core."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import math
import sys
from threading import Lock
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import eccodes as ec

from point_sources import component_cli, fetch, km, stamp, towns


CACHE = "https://wis2globalcache.s3.us-east-1.amazonaws.com/"
PREFIX = "data/za-weathersa/data/core/weather/surface-based-observations/synop/WIGOS_"
# Some ecCodes builds initialize BUFR grammar tables using a non-reentrant lexer.
DECODE_LOCK = Lock()
# Only named town-area and weather proxies with an explicit physical connection.
# Airport, offshore island and distant mountain/valley exposures are omitted.
STATIONS = {
    "paternoster": ("0-20000-0-68712", "Cape Columbine", 8),
    "langebaan": ("0-20000-0-68811", "Geelbek", 16),
    "vredendal": ("0-20000-0-68614", "Vredendal", 5),
    "malmesbury": ("0-20000-0-68715", "Malmesbury", 5),
    "lambertsbaai": ("0-20000-0-68613", "Lamberts Bay", 10),
}


def latest_reports(station, now):
    prefix = f"{PREFIX}{station}_"
    params = {"list-type": "2", "prefix": prefix}
    reports = []
    while True:
        data, _ = fetch(CACHE + "?" + urlencode(params), limit=2_000_000)
        root = ET.fromstring(data)
        for row in root.findall("{*}Contents"):
            key = row.findtext("{*}Key", "")
            if not key.startswith(prefix) or not key.endswith(".bufr4"):
                continue
            time = datetime.strptime(key[len(prefix):-6], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            if now - timedelta(hours=30) <= time <= now:
                reports.append((time, key, row.findtext("{*}ETag"), row.findtext("{*}LastModified")))
        next_page = root.findtext("{*}NextContinuationToken")
        if not next_page:
            return sorted(reports, reverse=True)
        params["continuation-token"] = next_page


def number(handle, name, low, high):
    key = "#1#" + name
    if not ec.codes_is_defined(handle, key) or ec.codes_is_missing(handle, key):
        return None
    value = ec.codes_get(handle, key)
    return float(value) if low <= value <= high else None


def humidity(measured, temp_k, dew_k):
    if measured is not None:
        return round(measured, 1), "reported relative humidity"
    if temp_k is None or dew_k is None or dew_k > temp_k + .2:
        return None, None
    # WMO Guide No. 8 approximation for saturation vapour pressure over water.
    t, d = temp_k - 273.15, dew_k - 273.15
    if min(t, d) < -45 or max(t, d) > 60:
        return None, None
    rh = 100 * math.exp(17.62*d/(243.12+d) - 17.62*t/(243.12+t))
    return round(min(100, rh), 1), "derived from reported temperature and dew point"


def decode(data, station, item, centre, max_km):
    time, key, etag, modified = item
    if data[:4] != b"BUFR" or int.from_bytes(data[4:7], "big") != len(data):
        raise ValueError("Incomplete BUFR message")
    handle = ec.codes_new_from_message(data)
    try:
        ec.codes_set(handle, "unpack", 1)
        if ec.codes_get(handle, "numberOfSubsets") != 1 or ec.codes_get(handle, "edition") != 4:
            raise ValueError("Unexpected SYNOP encoding")
        descriptors = ("wigosIdentifierSeries", "wigosIssuerOfIdentifier", "wigosIssueNumber",
                       "wigosLocalIdentifierCharacter")
        actual = "-".join(str(ec.codes_get(handle, field)).strip() for field in descriptors)
        when = datetime(*(ec.codes_get(handle, field) for field in ("year", "month", "day", "hour", "minute")), tzinfo=UTC)
        if actual != station or when != time:
            raise ValueError("WIGOS identifier or observation time disagrees with the object key")
        lat, lon = number(handle, "latitude", -90, 90), number(handle, "longitude", -180, 180)
        if lat is None or lon is None or km(centre, lat, lon) > max_km:
            raise ValueError("Station location differs from the reviewed town proxy")
        temp = number(handle, "airTemperature", 180, 340)
        rh, method = humidity(number(handle, "relativeHumidity", 0, 100), temp,
                              number(handle, "dewpointTemperature", 180, 340))
        wind = number(handle, "windSpeed", 0, 115)
        direction = number(handle, "windDirection", 0, 360)
        if all(v is None for v in (temp, rh, wind)):
            raise ValueError("SYNOP has no applicable weather fields")
        return {"station_id": station, "station_name": ec.codes_get(handle, "stationOrSiteName").strip(),
                "latitude": round(lat, 5), "longitude": round(lon, 5), "distance_km": km(centre, lat, lon),
                "observed_utc": stamp(when), "available_utc": stamp(datetime.fromisoformat(modified)),
                "temperature_c": round(temp - 273.15, 2) if temp is not None else None,
                "humidity_percent": rh, "humidity_method": method, "wind_speed_ms": wind,
                "wind_from_deg": None if wind == 0 or direction is None else direction % 360,
                "source_url": CACHE + key, "source_etag": etag}
    finally:
        ec.codes_release(handle)


def update_stations(previous, *, now=None):
    now = now or datetime.now(UTC)
    centres = towns()
    old = (previous or {}).get("towns", {})
    history = (previous or {}).get("history", {})
    cutoff = stamp(now - timedelta(hours=12))

    def update(slug):
        station, _, limit = STATIONS[slug]
        prior = old.get(slug)
        if prior and prior.get("station_id") != station:
            prior = None
        retained = {r["observed_utc"]: r for r in history.get(slug, ())
                    if r["station_id"] == station and r["observed_utc"] >= cutoff}
        if prior and prior["observed_utc"] >= cutoff:
            retained[prior["observed_utc"]] = prior
        failed = False
        try:
            reports = latest_reports(station, now)
        except (OSError, ValueError, ET.ParseError) as err:
            print(f"{slug} WIS2 index: {err}", file=sys.stderr)
            failed = True
            reports = []
        for item in reports:
            time, key, etag, _ = item
            when = stamp(time)
            if when < cutoff and (prior or retained):
                break  # First run may still recover the newest observation from a quiet station.
            cached = retained.get(when)
            if cached and cached.get("source_etag") == etag:
                continue
            try:
                data, _ = fetch(CACHE + key, limit=128_000, headers={"If-Match": etag})
                with DECODE_LOCK:
                    retained[when] = decode(data, station, item, centres[slug], limit)
            except (OSError, ValueError, ec.CodesInternalError) as err:
                print(f"{slug} WIS2 report: {err}", file=sys.stderr)
                failed = True
        records = [retained[t] for t in sorted(retained)]
        latest = records[-1] if records and (not prior or records[-1]["observed_utc"] >= prior["observed_utc"]) else prior
        return slug, latest, [r for r in records if r["observed_utc"] >= cutoff], failed

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(update, STATIONS))
    return {"source": "South African Weather Service via WMO WIS2 core",
            "towns": {slug: r for slug, r, _, _ in results if r},
            "history": {slug: history for slug, _, history, _ in results if history},
            "refresh_errors": [slug for slug, _, _, failed in results if failed]}


if __name__ == "__main__":
    component_cli(update_stations)
