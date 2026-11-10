"""Run once from cron. Only the compact JSON products survive a worker restart."""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path

from atmosphere import update_forecast
from baseline import TOWNS, update_baseline, utc
from marine import update_marine
from stations import update_stations
from verification import score

COLLECTORS = {"atmosphere": update_forecast, "baseline": update_baseline,
              "marine": update_marine, "stations": update_stations}


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write(path, obj):
    # Separate files in a single git commit make the published snapshot and ledger atomic.
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        json.dump(obj, out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        out.write("\n")
    os.replace(temp, path)


def main(folder):
    folder.mkdir(parents=True, exist_ok=True)
    previous = load(folder / "latest.json")
    ledger = load(folder / "verification.json")
    feed = {"schema_version": 1, "centres": TOWNS}
    issues = []
    for name, collect in COLLECTORS.items():
        old = previous.get(name) or {}
        try:
            result = collect(old)
            if not result.get("towns"):
                raise ValueError("source returned no towns")
            feed[name] = result
            if result.get("refresh_errors"):
                logging.warning("%s partial refresh: %s", name, result["refresh_errors"])
                issues.append(name)
        except Exception:
            logging.exception("Could not refresh %s. Keeping original source dates.", name)
            feed[name] = old
            issues.append(name)
        print(f"{name}: {len(feed[name].get('towns', {}))} towns", flush=True)
    now = dt.datetime.now(dt.timezone.utc)
    if set(feed["atmosphere"].get("towns", {})) != set(TOWNS):
        raise RuntimeError("A complete 15-town forecast is required before publication")
    if now - dt.datetime.fromisoformat(feed["atmosphere"]["run_utc"]) > dt.timedelta(hours=24):
        logging.warning("Atmospheric cycle is over 24 hours old: %s", feed["atmosphere"]["run_utc"])
        issues.append("atmosphere")
    for slug, report in feed["baseline"].get("towns", {}).items():
        if report.get("stale") or now - dt.datetime.fromisoformat(report["updated_utc"]) > dt.timedelta(hours=24):
            logging.warning("MET baseline for %s is stale: %s", slug, report["updated_utc"])
            issues.append("baseline")
    feed["generated_utc"] = utc(now)
    feed["scores"] = score(feed, ledger, now)
    feed["refresh_errors"] = sorted(set(issues))
    write(folder / "verification.json", ledger)
    write(folder / "latest.json", feed)
    return 1 if issues else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    raise SystemExit(main(parser.parse_args().data))
