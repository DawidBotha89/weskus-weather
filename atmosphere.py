"""Seven-day town-centre forecasts from the public ECMWF IFS operational archive."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.client import IncompleteRead
import json
import math
from pathlib import Path
from threading import Lock
from urllib.request import Request, urlopen

import eccodes as ec


UTC = timezone.utc
TOWNS = json.loads(Path(__file__).with_name("towns.json").read_text(encoding="utf-8"))
MODEL = "lw-ifs-0p25-bilinear-v1"
STEPS = (*range(0, 145, 3), 150, 156, 162, 168)
MIRRORS = ("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
           "https://storage.googleapis.com/ecmwf-open-data", "https://data.ecmwf.int/forecasts")
UNITS = {"2t": "K", "2d": "K", "10u": "m s**-1", "10v": "m s**-1",
         "msl": "Pa", "tp": "m", "tcc": "(0 - 1)"}
DECODE_LOCK = Lock()


def utc(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def source_key(run, lead):
    return f"{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc"


def read_source(path, offset=None, count=None):
    errors = []
    for mirror in MIRRORS:
        url = f"{mirror}/{path}"
        headers = {"User-Agent": "Lekker-Weskus-weather/1.0"}
        if offset is not None:
            headers["Range"] = f"bytes={offset}-{offset + count - 1}"
        try:
            with urlopen(Request(url, headers=headers), timeout=20) as response:
                if count is not None:
                    expected = f"bytes {offset}-{offset + count - 1}/"
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                        raise ValueError("mirror did not return the requested range")
                limit = count if count is not None else 500_000
                data = response.read(limit + 1)
                if not data or len(data) > limit or (count is not None and len(data) != count):
                    raise ValueError("unexpected byte count")
                return data
        except (OSError, ValueError, IncompleteRead) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("ECMWF mirrors unavailable: " + " | ".join(errors))


def field_groups(index):
    fields = {}
    for line in index.splitlines():
        record = json.loads(line)
        name = record.get("param")
        if name not in UNITS or record.get("levtype") != "sfc":
            continue
        offset, size = int(record["_offset"]), int(record["_length"])
        if name in fields or offset < 0 or not 0 < size < 8_000_000:
            raise ValueError(f"Invalid surface index entry for {name}")
        fields[name] = (offset, size, name)
    if fields.keys() != UNITS.keys():
        raise ValueError(f"Incomplete ECMWF surface index: missing {UNITS.keys() - fields.keys()}")
    # Each message is independently decodable. Nearby messages can share a single HTTP range.
    groups = []
    for start, size, name in sorted(fields.values()):
        if groups:
            last_start, last_size, _ = groups[-1][-1]
            gap = start - (last_start + last_size)
            if gap < 0:
                raise ValueError("Overlapping ECMWF index entries")
            if gap <= 16_384:
                groups[-1].append((start, size, name))
                continue
        groups.append([(start, size, name)])
    return groups


def grid_stencil(lat, lon, west):
    x, y = ((lon - west) % 360) * 4, (90 - lat) * 4
    i, j = math.floor(x), math.floor(y)
    if not 0 <= j < 720:
        raise ValueError("Town outside supported grid")
    dx, dy = x - i, y - j
    return [(row * 1440 + col % 1440, wx * wy)
            for row, wy in ((j, 1 - dy), (j + 1, dy))
            for col, wx in ((i, 1 - dx), (i + 1, dx))]


@lru_cache(maxsize=4)
def grid_locations(west):
    stencils = {slug: grid_stencil(*position, west) for slug, position in TOWNS.items()}
    return stencils, sorted({i for stencil in stencils.values() for i, _ in stencil})


def sample(data, field, run, lead):
    handle = ec.codes_new_from_message(data)
    try:
        expected = {"shortName": field, "dataDate": int(run.strftime("%Y%m%d")),
                    "dataTime": run.hour * 100, "endStep": lead, "stepUnits": 1,
                    "units": UNITS[field], "stepType": "accum" if field == "tp" else "instant",
                    "gridType": "regular_ll", "scanningMode": 0, "Ni": 1440, "Nj": 721,
                    "iDirectionIncrementInDegrees": 0.25, "jDirectionIncrementInDegrees": 0.25,
                    "latitudeOfFirstGridPointInDegrees": 90}
        if field == "tp":
            expected["startStep"] = 0
        for key, target in expected.items():
            value = ec.codes_get(handle, key)
            if value != target:
                raise ValueError(f"Unexpected {field} {key}: {value} (expected {target})")
        stencils, indices = grid_locations(ec.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
        cells = dict(zip(indices, ec.codes_get_elements(handle, "values", indices), strict=True))
        missing = ec.codes_get(handle, "missingValue")
        if any(v == missing or not math.isfinite(v) for v in cells.values()):
            raise ValueError(f"Missing {field} grid value")
        return {slug: sum(cells[i] * w for i, w in stencil) for slug, stencil in stencils.items()}
    finally:
        ec.codes_release(handle)


def read_step(run, lead, index=None):
    key = source_key(run, lead)
    groups = field_groups(index if index is not None else read_source(key + ".index"))
    values = {}
    for group in groups:
        first = group[0][0]
        last, length, _ = group[-1]
        data = read_source(key + ".grib2", first, last + length - first)
        # ecCodes may have a non-thread-safe build. Only HTTP requests run in parallel.
        with DECODE_LOCK:
            for start, size, name in group:
                pos = start - first
                values[name] = sample(data[pos:pos + size], name, run, lead)
    return values


def build_row(values, valid, interval, previous_total):
    temperature = values["2t"] - 273.15
    dew = values["2d"] - 273.15
    humidity = 100 * math.exp(17.625 * (dew / (243.04 + dew) - temperature / (243.04 + temperature)))
    u, v = values["10u"], values["10v"]
    speed = math.hypot(u, v)
    total = values["tp"] * 1000
    delta = None if previous_total is None else total - previous_total
    # Negative differences beyond packing noise invalidate this interval only.
    rain = round(max(0, delta), 3) if delta is not None and delta >= -0.05 else None
    return {"valid_utc": utc(valid), "temperature_c": round(temperature, 2),
            "humidity_pct": round(max(0, min(100, humidity)), 1), "dewpoint_c": round(dew, 2),
            "wind_u_ms": round(u, 3), "wind_v_ms": round(v, 3), "wind_speed_ms": round(speed, 2),
            "wind_from_deg": None if speed < 0.1 else round(math.degrees(math.atan2(-u, -v))) % 360,
            "pressure_hpa": round(values["msl"] / 100, 2),
            "cloud_pct": round(max(0, min(1, values["tcc"])) * 100, 1),
            "rain_mm": rain, "rain_interval_hours": interval}


def complete(previous, run):
    if not isinstance(previous, dict) or previous.get("model") != MODEL or previous.get("run_utc") != utc(run):
        return False
    towns = previous.get("towns")
    if not previous.get("fetched_utc") or not isinstance(towns, dict) or towns.keys() != TOWNS.keys():
        return False
    stamps = [utc(run + timedelta(hours=step)) for step in STEPS]
    required = {"temperature_c", "humidity_pct", "wind_speed_ms", "wind_from_deg",
                "pressure_hpa", "cloud_pct", "rain_mm", "rain_interval_hours"}
    return all(isinstance(rows, list) and len(rows) == len(stamps) and all(
        isinstance(row, dict) and required <= row.keys() and row.get("valid_utc") == stamp
        for row, stamp in zip(rows, stamps, strict=True)) for rows in towns.values())


def update_forecast(previous):
    now = datetime.now(UTC)
    current = now.replace(hour=12 if now.hour >= 12 else 0, minute=0, second=0, microsecond=0)
    for run in (current, current - timedelta(hours=12), current - timedelta(hours=24)):
        if complete(previous, run):
            return previous
        try:
            last_index = read_source(source_key(run, STEPS[-1]) + ".index")
            field_groups(last_index)
            break
        except (RuntimeError, ValueError):
            continue
    else:
        raise RuntimeError("No complete seven-day ECMWF 00/12 UTC forecast cycle found")

    def get_step(lead):
        return read_step(run, lead, last_index if lead == STEPS[-1] else None)

    towns = {slug: [] for slug in TOWNS}
    totals = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, (lead, values) in enumerate(zip(STEPS, pool.map(get_step, STEPS), strict=True)):
            for slug in towns:
                town = {name: readings[slug] for name, readings in values.items()}
                interval = lead - STEPS[n - 1] if n else None
                towns[slug].append(build_row(town, run + timedelta(hours=lead), interval, totals.get(slug)))
                totals[slug] = town["tp"] * 1000
    return {"model": MODEL, "run_utc": utc(run), "fetched_utc": utc(datetime.now(UTC)),
            "grid_degrees": 0.25, "towns": towns,
            "rain_definition": "Total precipitation (mm) over the preceding native forecast interval",
            "credit": {"attribution": f"© {run.year} European Centre for Medium-Range Weather Forecasts (ECMWF). "
                       "This output is based on data and products of the European Centre for Medium-Range Weather "
                       "Forecasts (ECMWF). Lekker Weskus interpolates IFS to town centres, derives humidity and "
                       "wind from model fields, and converts units and precipitation intervals.",
                       "source_url": "https://www.ecmwf.int/en/forecasts/datasets/open-data",
                       "licence_url": "https://creativecommons.org/licenses/by/4.0/",
                       "terms_url": "https://apps.ecmwf.int/datasets/licences/general/",
                       "disclaimer": "ECMWF does not accept any liability whatsoever for any error or omission "
                       "in the data, their availability, or for any loss or damage arising from their use."}}
