"""NOAA primary ocean swell and daily offshore SST at fixed, named open-water cells."""

from datetime import UTC, datetime, timedelta
import hashlib
import io
import math
import sys
from urllib.error import HTTPError

import eccodes as ec
import h5py

from point_sources import component_cli, fetch, km, stamp, towns


GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
OISST = "https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr"
# Towns point to named open water. GFS and OISST grid centres are offset half a cell.
AREAS = {
    "columbine": ("Offshore west of Cape Columbine", (-32.75, 17.50), (-32.875, 17.625), ["paternoster"]),
    "saldanha": ("Offshore west of Saldanha Bay", (-33.00, 17.75), (-33.125, 17.625), ["saldanha", "langebaan"]),
    "helena": ("Offshore in St Helena Bay", (-32.50, 18.00), (-32.625, 18.125),
               ["st-helena-bay", "velddrif", "dwarskersbos"]),
    "yzerfontein": ("Offshore west of Yzerfontein", (-33.25, 17.75), (-33.375, 17.875), ["yzerfontein"]),
    "elands": ("Offshore west of Elandsbaai", (-32.25, 18.00), (-32.375, 18.125), ["elandsbaai"]),
    "lamberts": ("Offshore west of Lambertsbaai", (-32.00, 18.00), (-32.125, 18.125), ["lambertsbaai"]),
}


def wave_index(run, step):
    key = f"gfs.{run:%Y%m%d}/{run:%H}/wave/gridded/gfswave.t{run:%H}z.global.0p25.f{step:03d}.grib2"
    url = f"{GFS}/{key}"
    blob, _ = fetch(url + ".idx", limit=100_000)
    lines = [line.split(":") for line in blob.decode("ascii").splitlines()]
    fields = {}
    for row, nxt in zip(lines, lines[1:]):
        if len(row) >= 6 and row[3] in ("SWELL", "SWPER") and row[4] == "1 in sequence":
            fields[row[3]] = int(row[1]), int(nxt[1]) - 1
    if set(fields) != {"SWELL", "SWPER"}:
        raise ValueError("Primary swell partition missing from NOAA GFS Wave index")
    return url, fields, hashlib.sha256(blob).hexdigest()


def sample_wave(data, field, run, step):
    handle = ec.codes_new_from_message(data)
    try:
        expected = {"dataDate": int(run.strftime("%Y%m%d")), "dataTime": run.hour * 100, "endStep": step,
                    "stepUnits": 1, "units": "m" if field == "SWELL" else "s", "gridType": "regular_ll"}
        if any(ec.codes_get(handle, k) != v for k, v in expected.items()):
            raise ValueError("Unexpected wave product time, field units or grid")
        values = {}
        missing = ec.codes_get(handle, "missingValue")
        for area, (_, (lat, lon), _, _) in AREAS.items():
            cell = ec.codes_grib_find_nearest(handle, lat, lon)[0]
            upper = 40 if field == "SWELL" else 45
            if abs(cell.lat - lat) > .002 or abs(cell.lon - lon) > .002:
                raise ValueError("Wave product moved the reviewed offshore grid cell")
            if cell.value == missing or not 0 <= cell.value <= upper or (field == "SWPER" and cell.value == 0):
                continue
            values[area] = round(cell.value, 2)
        return values
    finally:
        ec.codes_release(handle)


def read_waves(now, cache, complete):
    # Nearest native 3-hour valid time; choose the most recent published 6-hour initialization.
    valid = (now + timedelta(minutes=90)).replace(minute=0, second=0, microsecond=0)
    valid = valid.replace(hour=valid.hour // 3 * 3)
    cycle = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    failures = []
    for i in range(4):
        run = cycle - timedelta(hours=i * 6)
        step = int((valid - run).total_seconds() / 3600)
        try:
            url, ranges, digest = wave_index(run, step)
            _, metadata = fetch(url, limit=0, method="HEAD")
            token = {"source_url": url, "index_sha256": digest, "etag": metadata.get("ETag")}
            if complete and cache == token:
                return token, None
            fields = {}
            for k, span in ranges.items():
                guards = {"If-Match": token["etag"]} if token["etag"] else {}
                data, _ = fetch(url, limit=2_000_000, byte_range=span, headers=guards)
                fields[k] = sample_wave(data, k, run, step)
            rows = {}
            for area, (_, (lat, lon), _, _) in AREAS.items():
                if area in fields["SWELL"] and area in fields["SWPER"]:
                    rows[area] = {"run_utc": stamp(run), "valid_utc": stamp(valid), "latitude": lat, "longitude": lon,
                                  "swell_height_m": fields["SWELL"][area], "swell_period_s": fields["SWPER"][area],
                                  "source_url": url}
            return token, rows
        except (OSError, ValueError, ec.CodesInternalError) as err:
            failures.append(f"{run.isoformat()} {err}")
    raise RuntimeError("No recent NOAA wave run available. " + " | ".join(failures))


def sample_sst(blob, day, url):
    with h5py.File(io.BytesIO(blob), "r") as file:
        ds = file["sst"]
        if (ds.shape != (1, 1, 720, 1440) or ds.attrs["units"].decode() != "Celsius"
                or file.attrs["time_coverage_start"].decode() != f"{day}T00:00:00Z"
                or file.attrs["time_coverage_end"].decode() != f"{day}T23:59:59Z"
                or file["time"].attrs["units"].decode() != "days since 1978-01-01 12:00:00"):
            raise ValueError("Unexpected OISST units, date or grid")
        valid = datetime(1978, 1, 1, 12, tzinfo=UTC) + timedelta(days=float(file["time"][0]))
        if valid.date() != day:
            raise ValueError("OISST time coordinate differs from named UTC day")
        fill = ds.attrs["_FillValue"].item()
        scale, offset = ds.attrs["scale_factor"].item(), ds.attrs["add_offset"].item()
        rows = {}
        for area, (_, _, (lat, lon), _) in AREAS.items():
            i, j = round((lat + 89.875) / .25), round((lon - .125) / .25)
            if (float(file["lat"][i]), float(file["lon"][j])) != (lat, lon):
                raise ValueError("OISST grid has moved")
            raw = ds[0, 0, i, j].item()
            value = raw * scale + offset
            if raw != fill and math.isfinite(value) and -2.5 <= value <= 40:
                rows[area] = {"valid_utc": stamp(valid), "temperature_c": round(value, 2), "latitude": lat,
                              "longitude": lon, "source_url": url}
        return rows


def read_sst(now, cache, complete):
    # A completed daily analysis covers 00–24 UTC. Prefer the latest published day.
    for age in range(1, 6):
        day = (now - timedelta(days=age)).date()
        base = f"{OISST}/{day:%Y%m}/oisst-avhrr-v02r01.{day:%Y%m%d}"
        for suffix in ("_preliminary.nc", ".nc"):
            url = base + suffix
            try:
                _, meta = fetch(url, limit=0, method="HEAD")
            except HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            token = {"source_url": url, "etag": meta.get("ETag"), "last_modified": meta.get("Last-Modified")}
            if complete and (token["etag"] or token["last_modified"]) and cache == token:
                return token, None
            headers = {"If-Match": token["etag"]} if token["etag"] else {}
            data, _ = fetch(url, limit=8_000_000, headers=headers)
            return token, sample_sst(data, day, url)
    raise RuntimeError("No OISST analysis available for the five previous UTC days")


def update_marine(previous, *, now=None):
    now = now or datetime.now(UTC)
    previous = previous or {}
    centres = towns()
    old = previous.get("towns", {})
    cache = dict(previous.get("cache", {}))
    result = {}
    errors = []
    for area, (name, _, _, slugs) in AREAS.items():
        for slug in slugs:
            prior = old.get(slug, {})
            result[slug] = {"area_name": name, **{k: prior.get(k) if prior.get("area_name") == name else None
                                                for k in ("wave", "sst")}}
    for field, reader in (("wave", read_waves), ("sst", read_sst)):
        try:
            complete = all(result[s][field] for s in result)
            token, updates = reader(now, cache.get(field), complete)
            if updates is None:
                continue
            for area, row in updates.items():
                for slug in AREAS[area][3]:
                    prior = result[slug][field]
                    # An older fallback must never overwrite a more recent valid time or model run.
                    if prior and (row["valid_utc"], row.get("run_utc", "")) < (prior["valid_utc"], prior.get("run_utc", "")):
                        continue
                    result[slug][field] = {**row, "distance_km": km(centres[slug], row["latitude"], row["longitude"])}
            cache[field] = token
        except (OSError, ValueError, KeyError, RuntimeError) as err:
            print(f"NOAA offshore {field}: {err}", file=sys.stderr)
            errors.append(field)
    return {"sources": {"wave": "NOAA/NCEP GFS Wave, first swell partition",
                        "sst": "NOAA/NCEI daily OISST v2.1 offshore analysis"},
            "towns": result, "cache": cache, "refresh_errors": errors}


if __name__ == "__main__":
    component_cli(update_marine)
