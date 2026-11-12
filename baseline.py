"""Save the same public forecast the site serves. The function does its own upstream caching."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import urllib.parse
import urllib.request
from pathlib import Path

TOWNS = json.loads(Path(__file__).with_name("towns.json").read_text())
ENDPOINT = "https://gwsluoymzmnzhhppuzcy.supabase.co/functions/v1/wind-forecast"


def utc(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def read_number(value, low, high):
    if type(value) in (int, float) and math.isfinite(value) and low <= value <= high:
        return value
    return None


def read_town(lat, lon):
    url = ENDPOINT + "?" + urllib.parse.urlencode({"lat": lat, "lng": lon})
    request = urllib.request.Request(url, headers={"User-Agent": "WeskusWeather/1.0 github.com/DawidBotha89/weskus-weather"})
    with urllib.request.urlopen(request, timeout=12) as response:
        report = json.load(response)
    hours = []
    for step in report.get("series") or report.get("hours") or []:
        valid = dt.datetime.fromisoformat(step["time"])
        wind = read_number(step.get("kmh"), 0, 290)
        direction = read_number(step.get("fromDeg"), 0, 360)
        period = read_number(step.get("rainPeriodH"), 1, 6)
        rain = read_number(step.get("rainMm"), 0, 1500) if period in (1, 6) else None
        hours.append({"valid_utc": utc(valid.astimezone(dt.timezone.utc)),
                      "temperature_c": read_number(step.get("tempC"), -30, 55),
                      "humidity_pct": read_number(step.get("humidityPct"), 0, 100),
                      "wind_from_deg": direction % 360 if direction is not None else None,
                      "pressure_hpa": read_number(step.get("pressureHpa"), 850, 1100),
                      "rain_mm": rain, "rain_period_h": int(period) if rain is not None else None,
                      "wind_speed_ms": round(wind / 3.6, 3) if wind is not None else None})
    if not hours or not report.get("updated"):
        raise ValueError("No dated baseline series")
    return {"hours": hours, "updated_utc": report["updated"], "stale": bool(report.get("stale")),
            "fetched_utc": utc(dt.datetime.now(dt.timezone.utc))}


def update_baseline(previous):
    towns = dict((previous or {}).get("towns", {}))
    failed_before = set((previous or {}).get("refresh_errors", []))
    errors = []
    now = dt.datetime.now(dt.timezone.utc)

    def update(slug):
        old = towns.get(slug, {})
        try:
            if (slug not in failed_before and old.get("hours") and not old.get("stale")
                and dt.timedelta(0) <= now - dt.datetime.fromisoformat(old["fetched_utc"]) < dt.timedelta(minutes=45)
                and dt.timedelta(0) <= now - dt.datetime.fromisoformat(old["updated_utc"]) <= dt.timedelta(hours=24)):
                return slug, old, False
        except (KeyError, TypeError, ValueError):
            pass
        try:
            return slug, read_town(*TOWNS[slug]), False
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logging.warning("Baseline %s: %s", slug, exc)
            return slug, old, True

    with ThreadPoolExecutor(max_workers=4) as pool:
        for slug, result, failed in pool.map(update, TOWNS):
            if result:
                towns[slug] = result
            if failed:
                errors.append(slug)
    return {"towns": towns, "credit": "MET Norway, CC BY 4.0, via the Lekker Weskus wind-forecast service",
            "refresh_errors": errors,
            "source_url": "https://api.met.no/doc/License"}
