"""Run once from cron. Only the compact JSON products survive a worker restart."""
import argparse
from copy import deepcopy
import datetime as dt
import json
import logging
import os
from pathlib import Path
import time

from atmosphere import complete, update_forecast
from baseline import TOWNS, update_baseline, utc
from marine import AREAS, update_marine
from stations import STATIONS, update_stations
from verification import score

COLLECTORS = {"atmosphere": update_forecast, "baseline": update_baseline,
              "marine": update_marine, "stations": update_stations}
RETRY_DELAYS = (20, 60)
COASTAL_TOWNS = {slug for area in AREAS.values() for slug in area[3]}


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write(path, obj):
    # Separate files in a single git commit make the published snapshot and ledger atomic.
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        json.dump(obj, out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        out.write("\n")
    os.replace(temp, path)


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Source timestamp must include a UTC offset")
    return parsed


def validate(name, result):
    """Reject malformed responses before they can replace a last good source."""
    if not isinstance(result, dict) or not isinstance(result.get("towns"), dict):
        raise ValueError("Source must contain a town dictionary")
    if not set(result["towns"]) <= set(TOWNS):
        raise ValueError("Unexpected town in source")
    json.dumps(result, allow_nan=False)
    if not isinstance(result.get("refresh_errors", []), list):
        raise ValueError("Invalid source error list")
    if name == "atmosphere":
        if not complete(result, timestamp(result["run_utc"])):
            raise ValueError("Incomplete 15-town atmospheric forecast")
        timestamp(result["fetched_utc"])
    for report in result["towns"].values():
        if name in ("atmosphere", "baseline"):
            rows = report if name == "atmosphere" else report["hours"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("Source returned an empty forecast")
            for row in rows:
                timestamp(row["valid_utc"])
                for field in ("temperature_c", "wind_speed_ms"):
                    value = row[field]
                    if value is not None and type(value) not in (int, float):
                        raise ValueError("Invalid forecast measurement")
        if name == "baseline":
            timestamp(report["updated_utc"])
            timestamp(report["fetched_utc"])
        if name == "marine":
            for field in ("wave", "sst"):
                if report.get(field):
                    timestamp(report[field]["valid_utc"])
        if name == "stations":
            timestamp(report["observed_utc"])
            if not report.get("station_id") or type(report["distance_km"]) not in (int, float):
                raise ValueError("Invalid observation provenance")
    if name == "stations":
        for records in result.get("history", {}).values():
            for record in records:
                timestamp(record["observed_utc"])
                if not record.get("station_id") or type(record["distance_km"]) not in (int, float):
                    raise ValueError("Invalid historical observation provenance")


def source_errors(name, result, now):
    errors = ["source_reported_error"] if result.get("refresh_errors") else []
    towns = result.get("towns", {})
    if name in ("atmosphere", "baseline") and set(towns) != set(TOWNS):
        errors.append("incomplete_town_coverage")
    if name == "atmosphere":
        run = timestamp(result["run_utc"])
        if not dt.timedelta(0) <= now - run <= dt.timedelta(hours=24):
            errors.append("stale_or_future_model_run")
    if name == "baseline":
        for slug, report in towns.items():
            if (report.get("stale")
                or not dt.timedelta(0) <= now - timestamp(report["updated_utc"]) <= dt.timedelta(hours=24)
                or not dt.timedelta(0) <= now - timestamp(report["fetched_utc"]) <= dt.timedelta(hours=2)):
                errors.append("stale_baseline:" + slug)
    if name == "marine":
        for slug in COASTAL_TOWNS:
            for field, max_age in (("wave", dt.timedelta(hours=24)), ("sst", dt.timedelta(days=5))):
                record = towns.get(slug, {}).get(field)
                if not record or not -dt.timedelta(hours=3) <= now - timestamp(record["valid_utc"]) <= max_age:
                    errors.append("missing_or_stale_" + field + ":" + slug)
    return errors


def refresh(name, collect, previous):
    state = deepcopy(previous or {})
    if state:
        try:
            validate(name, state)
        except (KeyError, TypeError, ValueError):
            state = {}
    errors = []
    for attempt in range(1, 4):
        if attempt > 1:
            time.sleep(RETRY_DELAYS[attempt - 2])
        try:
            result = collect(deepcopy(state))
            validate(name, result)
            errors = source_errors(name, result, dt.datetime.now(dt.timezone.utc))
            # Partial responses can advance good towns while failed towns retain their own dates.
            if name != "atmosphere":
                result["towns"] = {**state.get("towns", {}), **result["towns"]}
            state = result
            if not errors:
                return state, attempt, []
            logging.warning("%s attempt %s incomplete: %s", name, attempt, errors)
        except Exception:
            logging.exception("%s attempt %s failed; retaining validated source data.", name, attempt)
            errors = ["collector_failed"]
    return state, attempt, errors


def station_warnings(stations, now):
    warnings = []
    for slug in STATIONS:
        report = stations.get("towns", {}).get(slug)
        if not report:
            warnings.append({"source": "stations", "code": "observation_unavailable", "town": slug})
            continue
        age = (now - timestamp(report["observed_utc"])).total_seconds() / 3600
        if age < 0 or age > 6:
            warnings.append({"source": "stations", "code": "observation_stale", "town": slug,
                             "observed_utc": report["observed_utc"], "age_hours": round(age, 2)})
    return warnings


def main(folder):
    folder.mkdir(parents=True, exist_ok=True)
    previous = load(folder / "latest.json")
    ledger = load(folder / "verification.json")
    feed = {"schema_version": 1, "centres": TOWNS}
    attempted = utc(dt.datetime.now(dt.timezone.utc))
    attempts, errors = {}, {}
    for name, collect in COLLECTORS.items():
        feed[name], attempts[name], failure = refresh(name, collect, previous.get(name))
        if failure:
            errors[name] = failure
        print(f"{name}: {len(feed[name].get('towns', {}))} towns", flush=True)
    now = dt.datetime.now(dt.timezone.utc)
    if set(feed["atmosphere"].get("towns", {})) != set(TOWNS):
        raise RuntimeError("A complete 15-town forecast is required before publication")
    # A long run can cross a freshness boundary after an early collector succeeds.
    for name, result in feed.items():
        if name in COLLECTORS and result:
            failure = source_errors(name, result, now)
            if failure:
                errors[name] = sorted(set(errors.get(name, []) + failure))
                if name == "baseline":
                    for slug, report in result["towns"].items():
                        if "stale_baseline:" + slug in failure:
                            report["stale"] = True
    feed["generated_utc"] = utc(now)
    feed["scores"] = score(feed, ledger, now)
    feed["refresh_errors"] = sorted(errors)
    feed["health"] = {"attempted_utc": attempted,
                      "last_success_utc": utc(now) if not errors else previous.get("health", {}).get("last_success_utc"),
                      "status": "degraded" if errors else "complete", "attempts": attempts,
                      "errors": errors, "warnings": station_warnings(feed["stations"], now)}
    write(folder / "verification.json", ledger)
    write(folder / "latest.json", feed)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("snapshot_written=true\n")
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    raise SystemExit(main(parser.parse_args().data))
