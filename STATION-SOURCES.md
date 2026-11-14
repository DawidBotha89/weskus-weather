# Station coverage investigation, 14 November 2026

The collector now combines two encodings of original South African Weather Service observations on unrestricted WMO WIS2 **core** paths. It does not purchase data, scrape private stations, infer observations from forecasts, or claim sensors in all 15 towns.

## What was missing

The individual `za-weathersa/.../synop/WIGOS_...bufr4` objects supplied three reviewed proxies but missed the main 00/06/12/18 UTC reports. The prefixes for Malmesbury and Lamberts Bay were empty during this check. Their WMO identifiers were correct: the observations were in the original SAWS `SMZA02 FAPR` text bulletin. Both DWD and JMA relay that bulletin to WIS2 core. The implementation tries DWD, then JMA if the first bridge has access/decoding errors. These are independent publishers in the same AWS cache, not independent physical storage providers.

Actual public bulletin inspected: [14 November 2026, 18 UTC](https://wis2globalcache.s3.us-east-1.amazonaws.com/data/de-dwd-gts-to-wis2/data/core/S/M/Z/A/02/FAPR/A_SMZA02FAPR141800_C_EDZW_20261114183904_81233645). The [NOAA original bulletin mirror](https://tgftp.nws.noaa.gov/data/raw/sm/smza02.fapr..txt) independently contained the same groups; the implementation uses WIS2, not NOAA. Cache URLs are ephemeral and can expire.

Cold retrieval recovered all four main observation hours for each of the five stations. The latest Malmesbury reading was 18:00 UTC, 23.8 C, wind 140 degrees at 3 knots (1.5433 m/s). Lamberts Bay Nortier was 18:00 UTC, 18.1 C, wind 200 degrees at 7 knots (3.6011 m/s). These are observations of their station exposures, not proof of forecast accuracy. The three existing BUFR stations had 19:00 UTC observations. The retrieval had zero source errors, 115 retained readings and a pretty-printed JSON size of approximately 127 KB.

## Reviewed metadata and coverage

Coordinates for text reports come from the official [OSCAR/Surface WMDR API](https://oscar.wmo.int/surface/rest/api/wmd/download/0-20000-0-68715). The identifier suffix selects each station's XML. The official [SAWS station catalogue](https://wis.weathersa.co.za/oapi/collections/stations/items?f=json&limit=1000) independently confirms the three existing sites but does not enumerate every station seen in the GTS bulletin. OSCAR marked the five sites operational at inspection. `station_registry.json` preserves the reviewed coordinates, names and exposure limits; it does not automatically trust new discoveries.

| Town | WMO ID | Official station | OSCAR latitude, longitude | Town to OSCAR position |
| --- | --- | --- | --- | --- |
| Paternoster | 68712 | Cape Columbine | -32.8278, 17.8558 | computed in feed |
| Langebaan | 68811 | Geelbek | -33.1964, 18.1241 | 14.29 km |
| Vredendal | 68614 | Vredendal | -31.673, 18.496 | computed in feed |
| Malmesbury | 68715 | Malmesbury | -33.4725, 18.7183 | 1.61 km |
| Lambertsbaai | 68613 | Lamberts Bay Nortier | -32.0351, 18.3326 | 6.91 km |

Distances are explicitly geodesic station separation, not driving distances. Nortier is an inland farm proxy, Cape Columbine a lighthouse exposure, and Geelbek the southern lagoon. They should not be presented as in-town sensors. No verified compatible report was found for the other ten towns; `coverage` reports `unavailable` with a reason. Langebaanweg (68714) is active in the bulletin, but its airport exposure was not silently substituted for Saldanha or Vredenburg. Dassen Island (68814) is offshore and OSCAR records closure in 2012. Porterville is a different inland exposure about 24 km from Piketberg. Metadata availability alone does not establish a usable local observation feed.

### Position discrepancy affecting calibration

The actual BUFR positions differ from current OSCAR metadata: Geelbek reports -33.18334,18.11666 (1.61 km from OSCAR; 12.71 km from Langebaan), Cape Columbine -32.83334,17.85 (0.82 km from OSCAR), and Vredendal -31.66667,18.5 (0.80 km from OSCAR). The cause is unverified; do not describe these as known station relocations or silently normalize them. BUFR records retain their encoded positions. Text records use OSCAR, explicitly labelled `coordinate_source` and `coordinate_metadata_url`. `coverage` coordinates are registry positions, not substitutes for an observation's position.

Geelbek text observations should not be used to evaluate a correction fitted to the BUFR position under a 1 km sensor-position tolerance. They can still be shown as a qualified station proxy. Reconcile metadata or train/evaluate separate coordinate-qualified targets before combining these as one calibration truth. The collector itself does not apply forecast corrections.

## Decoding and source terms

[WMO's synop2bufr](https://github.com/World-Meteorological-Organization/synop2bufr) demonstrates the standard FM-12 conversion route and metadata requirement. Its [installation documentation](https://synop2bufr.readthedocs.io/en/latest/installation.html) lists additional conversion dependencies. This worker needs only a narrow section-1 parser and keeps the existing Python 3.12/ecCodes dependency set. The parser uses AAXX `iw` to distinguish m/s from knots, signed temperature/dewpoint groups, reported RH when encoded, missing values, variable/calm wind and extended wind speeds. It stops before section 3 maxima/minima. The [NOAA observing handbook](https://www.weather.gov/media/safety/ObservingHandbook1_2010_508_compliant.pdf) documents the same synoptic wind and temperature conventions. Unsupported fields are omitted; this is not a general FM-12 implementation.

The [WIS2 Global Cache open-data registry](https://registry.opendata.aws/wis2-global-cache/) states there are no restrictions on use and asks users to reference source information. Only `/data/core/` products are fetched. The [WMO WIS2 overview](https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-information-system-wis/wis2-overview) distinguishes core and recommended data. Retain original SAWS attribution and exact source URLs. Public access alone is not permission to scrape unrelated station sites.

## Retention and failure contract

`towns` and `history` retain the existing observation shape, with additive `source_name`, `source_format`, `coordinate_source`, `coordinate_metadata_url` for text, and `source_revision`. `available_utc` is the cache object's publication time; text `received_utc` records our retrieval time. Neither is a forecast issue time. Existing old reports keep their original timestamps and source URLs through failures.

History is bounded to **72 hours**, permitting missed runs to recover matches without a 30-day mobile payload. This cannot recover observations that expired upstream before retrieval. The public cache advertises short retention; the collector inspects up to 48 hours of available bulletins and 30 hours of individual BUFR objects. Frozen verification records are retained separately by the updater. `bulletin_cache` stores revisions only and is reused only when the corresponding decoded observation remains in history/latest. Corrected `CC*` text reports take precedence over delayed routine duplicates; overlapping BUFR wins because it carries explicit coordinates. A partial failure preserves validated old and newly decoded reports and sets `refresh_errors`; both-empty indexes are an error. A legitimate NIL report is not a station failure. Stale station warnings remain the updater's responsibility.

`coverage` contains every configured town: `report_available`, `awaiting_report`, or `unavailable`, with station metadata and observation time where applicable. `report_available` means a report exists, not that it is fresh. Do not turn that status into a claim of local calibration skill.

Run offline parser/integration checks with `python -B -m unittest test_station_synop test_point_sources`. Real bulletin groups in the tests are attributed above; fixtures test decoding only. A second live refresh checks cache reuse independently from those fixture tests.
