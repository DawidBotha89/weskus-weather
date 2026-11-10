"""I/O for the public station and offshore workers."""

import argparse
from datetime import UTC
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def towns():
    return json.loads(Path(__file__).with_name("towns.json").read_text(encoding="utf-8"))


def km(centre, lat, lon):
    a, b = map(math.radians, centre)
    c, d = map(math.radians, (lat, lon))
    chord = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return round(6371.0088 * 2 * math.asin(math.sqrt(min(1, chord))), 2)


def fetch(url, *, limit, headers=None, method="GET", byte_range=None):
    h = dict(headers or {})
    if byte_range:
        start, end = byte_range
        if not 0 <= start <= end or end - start + 1 > limit:
            raise ValueError("Invalid requested source byte range")
        h["Range"] = f"bytes={start}-{end}"
    with urlopen(Request(url, headers=h, method=method), timeout=20) as res:
        if byte_range and (res.status != 206 or not res.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/")):
            raise ValueError("Server returned a different GRIB byte range")
        data = res.read(limit + 1)
        if len(data) > limit or (byte_range and len(data) != end - start + 1):
            raise ValueError("Source payload has an unexpected length")
        return data, res.headers


def component_cli(update):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    previous = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    result = update(previous)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.out.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(result, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write("\n")
            f.close()
            os.replace(temp, args.out)
        finally:
            temp.unlink(missing_ok=True)
    print(f"{args.out}: {len(result['towns'])} towns")
