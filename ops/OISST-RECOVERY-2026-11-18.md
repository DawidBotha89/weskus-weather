# Sea-temperature source recovery — 18 November 2026

## Incident and cause

The last completely successful publication before the incident was15:02:28UTC (17:02SAST). Runs from16:02UTC failed the marine source check. Logs show `No OISST analysis available for the five previous UTC days`; the original NCEI address returned404 even for previously published files. Air forecasts, station collection, wave updates and the experimental tide component continued, while the snapshot correctly reported degraded health. The watchdog retried at its25minute interval; it could not repair an unavailable upstream address.

NOAA's [OISST CDR page](https://www.ncei.noaa.gov/products/climate-data-records/sea-surface-temperature-optimum-interpolation) directly links the original download, Amazon bucket and Google bucket. Its [cloud migration notice](https://www.ncei.noaa.gov/news/cloud-migration) warns that data folders can become unavailable and updates can be delayed. This is consistent with the observed outage; NOAA has not supplied an endpoint-specific root-cause confirmation. The [NOAA Oceanic CDR registry](https://registry.opendata.aws/noaa-cdr-oceanic/) confirms public, no-account access and permitted reuse with attribution.

## Repair and compatibility

The existing OISSTv2.1 collector now checks all three official locations for each candidate day, newest first. A timeout/server failure/download failure/invalid payload causes it to try another host. A failing host is skipped for the rest of that attempt to bound delays.404still allows checking another file variant/date. No new dependency, account or paid API; Python3.12, h5py3.16.0 and ecCodes2.50.0 unchanged.

Preserved protections: five-day source-date search, timestamp/unit/grid checks, fixed offshore cells, ETag preconditions, response-size cap, missing-value filtering, original observation dates and no replacement by older retained data. A newly fetched file must cover all six reviewed offshore areas (nine coastal towns). Cache reuse requires the same URL and revision plus complete retained coverage. An outage of all sources still produces an explicit error and retains last good readings with their original date; health/freshness thresholds and watchdog settings are not weakened.

## Verification

Seven new offline regression tests cover404recovery, newest-date priority, download failure and second-cloud recovery, cache isolation/reuse, corrupt/incomplete data, bounded host failures, five-day search and last-good retention with explicit failure. All67Python tests pass. Six Node boundary tests pass; the optional local site-parity test is unchanged and skipped when SITE_MOBILE is not set.

Live endpoint checks established originalHTTP404 and both documented cloud copiesHTTP200. The cloud copy for16November contains the same daily product, not a newly observed temperature. Real decoding and full collector verification are recorded in the release PR receipt. The main weather forecast and coastal-tide defaults stay unchanged. No live database or credential change is needed.
