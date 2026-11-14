# West Coast weather

A public hourly update for 15 Western Cape towns. The site reads [the latest snapshot](https://raw.githubusercontent.com/DawidBotha89/weskus-weather/main/data/latest.json). All timestamps in the file are UTC. The site displays South African time.

* Weather comes from open ECMWF IFS surface fields. The collector interpolates four neighbouring grid cells to each town, calculates humidity and wind, and converts accumulated rainfall to an interval amount. The twice-daily seven-day forecast uses a 0.25° grid. It cannot resolve an individual beach or valley. [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
* The comparison baseline is a snapshot of the existing Lekker Weskus forecast powered by MET Norway. Both forecasts share ECMWF ancestry. Agreement between them does not establish local accuracy. [MET Norway](https://api.met.no/doc/locationforecast/datamodel) · [attribution and licence](https://api.met.no/doc/License)
* Weather observations come from named South African Weather Service stations on the WMO WIS2 core feed. Each observation carries its time, distance and source. Coastal exposure matters even when the gauge is nearby. [SAWS](https://wis.weathersa.co.za/) · [WIS2 public cache](https://registry.opendata.aws/wis2-global-cache/)
* NOAA GFS Wave provides primary swell height and period. NOAA OISST provides daily offshore sea-surface temperature. The selected cells and distances are included. These products describe the open sea and have no beach, lagoon or river thermometer. [Wave model](https://polar.ncep.noaa.gov/waves/) · [OISST](https://www.ncei.noaa.gov/products/optimum-interpolation-sst)
* The website calculates astronomical coastal tides separately from TICON-4 constituents using Neaps. Neither this feed nor the site supplies validated tides inside Langebaan Lagoon or the Berg River. [TICON-4](https://doi.org/10.17882/109129)

## Running and verifying

Use Python 3.12+, install `requirements.txt`, run `python -m unittest discover -p '*test*.py'`, then `python update.py --data data`. Keep this directory between runs. The public workflow requests a run at minute 24 each hour on a standard Ubuntu runner. GitHub can delay or drop scheduled events; this schedule alone does not guarantee hourly delivery. The workflow reports a failed refresh in its [run history](https://github.com/DawidBotha89/weskus-weather/actions/workflows/forecast.yml).

Each source gets up to three attempts, with 20-second and 60-second delays. Failed sources retain their original timestamps while successful sources can advance. `health.attempted_utc` records the refresh attempt; `health.last_success_utc` advances only when every required source passes coverage and freshness checks. `health.errors` identifies failed sources, and `health.warnings` identifies missing or stale station reports. A quiet station does not prevent the forecasts from updating. The workflow publishes only a newly written snapshot and keeps the forecast ledger in the same commit.

An independent Supabase watchdog targets minute 2 each hour, checks the feed served by the live site, records missed updates and retries failed publications. See [hourly operation and recovery](ops/README.md) for monitoring, credential rotation and deployment details.

For each town and valid time we freeze the first pair seen 6–24 hours ahead. Both predictions must be available together. We match the exact UTC time to a named station within 15 km, retain a rolling 30 days, and compute mean absolute error separately for temperature and wind. The verification ledger records the issue dates, collection time, original predictions and matched observations. An empty sample has no error estimate. The trial can have gaps when observations, baselines or scheduled runs are missing. Keep interpreting different leads, local exposures and seasons separately before choosing a forecast for the site.

## Langebaan station trial, 14 November 2026

The optional `calibration` component applies a frozen, bounded temperature offset and wind multiplier to the Langebaan forecast, targeting **Geelbek AWS, 12.71 km away**. It is experimental station-proxy output, not validated beach or town-centre weather. The raw forecast and MET baseline are unchanged. Only temperature and scalar wind are adjusted, at the four daily times represented in the training sample, within the next 24 hours. No humidity, rainfall, wind direction, marine or tide correction is inferred.

The ten-pair model expires **21 November 2026 at 19:02 UTC**. It does not automatically refit itself. Review prospective errors, obtain more independent weather days and nearby observations, then explicitly fit and version any replacement. The hourly raw feed continues after expiry. [Model, replay and limitations](research/geelbek-20261114/README.md).

`data/candidate-verification.json` separately freezes raw/MET/candidate triples together 6–24 hours before validity, after fitting. All three must use the identical Geelbek observation. The original two-way ledger is never backfilled with hindsight. The feed publishes `candidate_scores` by model version, including pending counts and mean absolute errors; zero observations means no evidence yet. All three data files publish in one commit. The site's main forecast remains MET Norway until a later decision supported by evidence.

## Station coverage, 14 November 2026

The station collector now combines SAWS individual BUFR reports and original SAWS main-hour SYNOP bulletins from the WIS2 core GTS bridges. This restores 00/06/12/18 UTC observations and adds verified Malmesbury and Lamberts Bay Nortier reports, bringing coverage to five named proxies. All other towns explicitly remain unavailable; airports and distant stations are not silently assigned. Station history retains 72 hours for recovery, with original observation times and source attribution.

Text bulletins use official OSCAR coordinates because they do not encode a position. Geelbek's OSCAR position differs by 1.61 km from the position in its BUFR reports. These are disclosed separately; text reports must not be treated as the identical training sensor for the existing correction. See [sources, decoding, coordinates and limitations](STATION-SOURCES.md).
