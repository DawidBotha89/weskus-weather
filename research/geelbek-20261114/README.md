# Geelbek experimental correction, 14 November 2026

This candidate adjusts the Langebaan IFS temperature and wind toward measured conditions at Geelbek AWS, WIGOS 0-20000-0-68811, 12.71 km from the town centre. It estimates the station proxy. Its behaviour elsewhere in Langebaan is unverified. The archived forecast was sampled at the town centre, so the fitted correction also includes differences in location and exposure.

The fixed method uses two SAST periods, day 06:00–17:59 and night 18:00–05:59. Each temperature correction is the median measured-minus-forecast error, bounded to ±4 °C. Each wind correction is the median measured/forecast ratio for predicted winds of at least 1 m/s, bounded to 0.5–1.25. These bounds are conservative implementation choices, not empirically validated physical limits. Wind remains nonnegative and no direction correction is inferred. At least two temperature pairs and two usable wind ratios are required per period. No parameter search or retuning followed the first holdout score.

## Retrospective evidence

The pinned ledger contained 12 matched Geelbek records. Two from 11 November lacked receipt evidence in the pinned station history and were excluded. Training used four pairs whose observations were received and archived before **13 November 06:00 UTC**. The six later observations stayed out of fitting. However, four of their original forecasts had been saved before the fitting cutoff, so the stricter issue-time replay contains only two daytime cases, on 14 November at 09:00 and 15:00 UTC.

| Comparison | N | Raw temperature MAE | Candidate temperature MAE | MET temperature MAE | Raw wind MAE | Candidate wind MAE | MET wind MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Later observation dates | 6 | 2.763 °C | 0.983 °C | 2.500 °C | 6.720 km/h | 2.712 km/h | 13.547 km/h |
| Forecast saved after fitting cutoff | 2 | 2.010 °C | 0.930 °C | 1.450 °C | 5.112 km/h | 4.671 km/h | 10.381 km/h |

Both comparisons are retrospective. The earlier performance audit had already exposed day/night error patterns, which informed the choice of model family. The stricter replay has no nighttime examples and shares one day's weather. These results justify collecting future candidate scores. They cannot establish validated local forecast accuracy or reliable uncertainty intervals. Full paired values, exact timestamps, coefficients and SHA-256 hashes for all four pinned input files are in [evaluation.json](evaluation.json).

## Frozen future candidate

[geelbek-model.json](geelbek-model.json) is a separate fit using all ten eligible archived pairs, with training cutoff **14 November 19:02:21 UTC**. Its version is `geelbek-two-period-median-v1-38cb25faa202`. Daytime temperature is adjusted by +2.125 °C and wind multiplied by 0.91523. Nighttime temperature is adjusted by −4 °C and wind multiplied by 0.5. The nighttime bounds are active, showing that these limited observations requested larger corrections.

The model expires seven days after its training cutoff. This is a conservative trial lifetime chosen for this experiment. Refresh requires an explicit new fit and version. Future performance needs a separately frozen score ledger retaining issue time, raw forecast, MET forecast, candidate, station identity and model version. Existing raw/MET ledger records remain the source for their comparisons.

`calibrated_candidate(atmosphere, model, now=None)` returns a labelled component with the Geelbek target, version, training cutoff, sample count, expiry, uncertainty explanation and `towns.langebaan` candidate rows. Rows contain only corrected temperature and scalar wind plus provenance. It emits future valid times up to 24 hours ahead and refuses models before creation or after expiry, and source runs over 24 hours old. The training leads were 16.04–23.96 hours. Shorter issued leads in the demonstration payload need separate future evaluation.

The future pilot emits only exact **05:00, 11:00, 17:00 and 23:00 SAST** forecasts, matching the four observed time slots in training. It requires a source run at most 24 hours old. Applying the two offsets as a continuous curve would produce a 6.125 °C correction jump at the day/night boundary, alongside a wind-factor jump. The API therefore omits the unobserved intermediate hours and makes no claim about the dawn/dusk transition. This deployment restriction preserves the fitted coefficients and retrospective experiment.

The API keeps uncertainty intervals null because this sample cannot establish their coverage. Outputs and models carry experimental status. [candidate.json](candidate.json) is a local demonstration generated from the pinned forecast, dated by its actual generation time.

## Reproduce

Use Python 3.12 or later with the standard library, from the workspace root.

```powershell
python -B -m unittest discover -s work/weskus-weather -p test_calibration.py -v
python -B work/weskus-weather/research_calibration.py --evidence tmp/weather-performance-20261114 --out tmp/geelbek-calibration-replay
```

Reproduction writes a newly dated model and example payload to the selected output folder. Preserve the original frozen model when scoring its future forecasts. The input measurement source is South African Weather Service via WMO WIS2 core, with the station's original observation and receipt timestamps retained. [WMO cache data policy](https://registry.opendata.aws/wis2-global-cache/)
