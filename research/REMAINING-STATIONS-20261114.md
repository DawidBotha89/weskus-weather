# Remaining West Coast station research — 14 November 2026

Read-only investigation, checked approximately 20:00–20:45 UTC. No accounts created, owners contacted, paid APIs used, collector code changed or data deployed. All distances below are geodesic station separation, not road distances.

**Result: no additional fresh, local and clearly redistributable station feed cleared all three tests in this pass.** There is a useful openly licensed historical Elands Bay archive; fresh official agriculture data with inadequate town proximity; and genuinely fresh CSIR Saldanha observations with restrictive published usage language. These are distinct routes, not additional claimed live coverage. Existing five WIS2 proxies remain unchanged.

## Best usable research route: Elands Bay historical hourly observations

Official DFFE/MIMS collection [10.15493/DEA.MIMS.10492023](https://data.ocean.gov.za/mims/catalog/10.15493/DEA.MIMS.10492023): latitude **-32.318261**, longitude **18.323393**, **2.19 km** from the site's Elandsbaai coordinate. Vaisala WXT520 raw hourly observations include air temperature, pressure, humidity, wind speed/direction and rainfall. Published coverage is **10 May 2018–28 September 2022**. The parent [Elands Bay collection](https://data.ocean.gov.za/mims/catalog/10.15493/dea.mims.26052307) also links July 2015–May 2018 records. This is precise local historical evidence, not a current station feed.

Dataset licence: **CC BY-SA 4.0**, with attribution and DOI citation required. This permits commercial reuse subject to its conditions. Current portal HTML returned HTTP 200 and now places downloads behind an audit form requiring name, email, organisation and licence acknowledgement. No form was submitted and no fictional identity supplied. File bytes, completeness, timezone and measurement units remain unverified. The index's broad statement that stations operated “until today” is not a fresh observation timestamp. No later linked Elands Bay collection was found.

Next viable step: an authorised person can retrieve that openly licensed archive through the declared form; then run a station-position historical hindcast comparison with explicit archive/model dates. Do not promote its 2022 endpoint into live coverage. Evidence: `mims-elands.html`, `mims-hourly.txt`.

## Official agriculture data: working anonymous endpoints, not ten new local sensors

Western Cape Department of Agriculture's [Weather Station Portal](https://gis.elsenburg.com/apps/wsp/) exposes its own ordinary public requests, with no key:

- GET `https://gis.elsenburg.com/apps/wsp/php/getstations.php`
- POST `https://gis.elsenburg.com/apps/wsp/php/getallcurrent.php`, empty body
- POST `https://gis.elsenburg.com/apps/wsp/php/gethourlyall.php`, form parameters `id`, `date_start`, `date_end`, `output=json`

All returned HTTP 200. The catalogue contains **37 stations**. Current data included **2026-11-14 22:00** logger readings. The raw timestamp has no offset; SAST is plausible but is **not yet verified by provider metadata**. Hourly sampling was confirmed with 24 consecutive rows from 13 November 01:00 to 14 November 00:00 for Rooi-Karoo, using `id=cir00616&date_start=2026-11-13&date_end=2026-11-14&output=json`. Treat the end date as potentially exclusive until formally tested.

**Crucial:** the same endpoint mixes `DataType: Logger` and `DataType: Forecast`. Never score a model against the latter. Fields include temperature, humidity, wind speed/direction, radiation, rainfall and derived ETo. Wind units/timezone and QC conventions require confirmation before integration. Portal text allows viewing/export and warns that readings are unverified, but no explicit open commercial redistribution licence was located. Free access is established; unrestricted redistribution is not.

| Candidate | ID | Verified catalogue position | Nearest relevant town | Separation | Latest logger example |
| --- | --- | --- | --- | --- | --- |
| Rooi-Karoo | cir00616 | -32.7891159, 18.8770077 | Piketberg | 16.98 km | 14 Nov 22:00, 22.98 C, humidity 51.3%, wind raw 1.39 |
| Langgewens | GWS00082 | -33.283658, 18.710225 | Riebeek Valley | 18.57 km | 14 Nov 22:00, 21.60 C, humidity 58.4%, wind raw 4.38 |
| Hopefield | GWS00009 | -33.037338, 18.434900 | Velddrif | 37.94 km | 14 Nov 22:00, 21.00 C, humidity 62.4%, wind raw 3.42 |
| Nortier | cir00494 | -32.034944, 18.332417 | Lambertsbaai, already covered | 6.92 km | 14 Nov 22:00, 16.67 C, humidity 77.2%, wind raw 1.25 |

Rooi-Karoo is a separate agricultural exposure, Langgewens is outside the Riebeek valley target, and Hopefield cannot represent the coast. They can support separately labelled regional analyses only after unit/licence checks, not fill local-town truth. Nortier might eventually provide redundancy and finer sampling for an existing proxy, not a sixth town.

Evidence: `elsenburg.html`, `elsenburg-main.js`, `elsenburg-stations.txt`, `elsenburg-current.txt`, `elsenburg-rooi-karoo-hourly.json`.

## Fresh Saldanha CSIR readings, but display-only published terms

Direct HTTP refresh of [Port Control](https://wavenet.csir.co.za/OnlineData/SaldanhaBay/saldanhabayweatherD1.htm) returned last data **14 November 2026 22:00**, page update 22:15. [Marcus Island](https://wavenet.csir.co.za/OnlineData/SaldanhaBay/saldanhabayweatherD2.htm) returned last data **22:10**, page update 22:15. Search-engine cached pages incorrectly appeared stuck in October; direct checks establish current page freshness.

The pages supply plotted weather images, not a verified structured observation export. Exact weather-sensor coordinates and units/cadence were not established; do not substitute the wave buoy's coordinates or infer cadence from a single timestamp. Marcus Island also has a distinct offshore exposure. Both pages claim reserved rights and expressly restrict the displayed estimates to display purposes, with no assumptions/conclusions. That does **not** support treating this as freely redistributable calibration truth. This is a current source lead, not an integrated source.

Evidence: `csir-port.txt`, `csir-island.txt`. The [CSIR/Transnet monitoring programme](https://wwwprod.csir.co.za/csir-signs-weather-monitoring-deal-transnet) confirms the institutional route; an appropriate open dataset release would be needed for a dependable automatic collector.

## Municipal air-quality meteorology: real network, access contract unresolved

The official [2023 National Air Quality Officer report](https://saaqis.environment.gov.za/Pagesfiles/2023%20NAQO%20Report.pdf) confirms meteorology at Laaiplek/Velddrif's municipal monitoring site and identifies Saldanha and Vredenburg stations. The [Western Cape 2014 State of Air report](https://d7.westerncape.gov.za/eadp/sites/eadp.westerncape.gov.za/files/atoms/files/SoAR%202014.pdf) gives historic continuous-monitor positions: Louwville **-32.9084167,18.0089444** and Saldanha town substation **-33.0010278,17.9450556**. These are **historical positions**, not verified current coordinates. It also records a Saldanha relocation and vandalism; do not equate dustfall sites with meteorology stations.

Live [SAAQIS](https://saaqis.environment.gov.za/) homepage returned HTTP 200. Its public client identified a regions request at `https://saaqis.environment.gov.za:44301/v1/envista/regions`. Ordinary anonymous guest-session request returned **401 Missing token**; the homepage's own public guest configuration also returned **401 Invalid API token**. No private credentials used and no authenticated access attempted. Saved public configuration was redacted. No usable public meteorological JSON, present-day coordinates, sampling schedule or redistribution licence was verified. This is a concrete access blocker, not evidence that the municipal sensors do not exist. Evidence: `saaqis.html` (redacted) and related public script captures. Do not embed a scraped token into production.

## Additional free-network checks

**NOAA MADIS/CWOP:** unlike a generic private-weather website, NOAA explicitly states that [CWOP observations have no restrictions](https://madis.ncep.noaa.gov/madis_cwop.shtml), with global coverage and five-minute ingestion/QC. Downloaded the actual [APRSWXNET station inventory](https://madis-data.ncep.noaa.gov/madisPublic1/data/stations/APRSWXNETStation.txt), HTTP 200, approximately 1.2 MB. The inventory's internal report date is **3 November 2026**, despite a more recent directory modification time. Coordinate filtering found 28 entries in the South Africa bounding box and **none within 15 km of the ten remaining towns**. Nearest to Darling was 17.79 km, a registration named Plattekloof Glen whose location needs independent validation; nearest to Elandsbaai was a Clanwilliam registration 69.41 km away. These are registrations, not proof of active observations. This route is genuinely free, but currently supplies no accepted local target. Evidence: `madis-aprs-stations.txt`.

**SAEON:** [observations monitor](https://observationsmonitor.saeon.ac.za/) describes raw data and authenticated site/data API access. Direct monitor and `/api/v1/sites` calls both returned **403 Forbidden** in this environment. No new station location or current row was verified. [SAEON policy](https://www.saeon.ac.za/wp-content/uploads/2021/03/SAEON-Data-Policy.pdf) supports open licensing in general, but inspect each dataset's actual licence and coverage; a policy is not proof of a local station. Rocherpan weather equipment is documented in [WRC's 2012 Water Wheel](https://www.wrc.org.za/wp-content/uploads/mdocs/WaterWheel_2012_05_September.pdf), but no fresh public endpoint was verified. A 2012 equipment photograph does not establish today's operation.

**Personal networks:** Wunderground pages identify leads such as IDWARS2 (Dwarskersbos) and ISTHEL67 (Sandy Point), while some town pages display another town's station. Search snapshots are not fresh station evidence. No verified direct owner feed and open redistribution terms were found. Netatmo's [official API documentation](https://dev.netatmo.com/apidocumentation/weather) is another possible account-based route, but no authenticated bounds query, precise local sensor position or commercial redistribution permission was established. Do not scrape third-party API keys or import public-facing displays as free observation archives.

## Town-by-town disposition

| Town | Best lead from this pass | Current accepted live coverage / exact blocker |
| --- | --- | --- |
| Yzerfontein | Personal station directories; agriculture catalogue | Unavailable. Town pages can select Langebaan sensors. No verified in-town owner endpoint/licence; nearest agriculture station 42.88 km. |
| St Helena Bay | Municipal air-quality monitoring; Sandy Point personal station lead | Unavailable. No current verified met rows/coordinates/open redistribution terms. Nearest agriculture station 49.07 km. |
| Velddrif | Municipal Laaiplek/Velddrif meteorology | Unavailable. Network documented, SAAQIS guest API 401; present sensor coordinates, cadence and licence unresolved. |
| Darling | Personal/Netatmo lead; old SAWS rainfall archive references | Unavailable. No verified owner feed/licence. Nearest agriculture station 32.41 km; CWOP 17.79 km and questionable registered location. A missing rainfall-bulletin row must not automatically mean zero rainfall. |
| Riebeek Valley | Langgewens agriculture logger | Unavailable as local truth. Fresh public rows, but 18.57 km and different valley exposure; redistribution and timezone unresolved. |
| Saldanha | CSIR Port Control; municipal town substation | Unavailable for automatic verification. CSIR current but display-only wording/no validated numeric export; SAAQIS access unresolved. |
| Vredenburg | Municipal Louwville AQM meteorology | Unavailable. Historical coordinate verified but present location/data not verified; SAAQIS 401. |
| Dwarskersbos | IDWARS2 personal station; historical Rocherpan equipment | Unavailable. Current location/freshness/owner endpoint and licence not verified. |
| Piketberg | Rooi-Karoo agriculture logger | Unavailable as local truth. 16.98 km, distinct farm exposure; redistribution and timezone unresolved. |
| Elandsbaai | DFFE WXT520 hourly archive, 2.19 km | **Local historical archive with open licence**, latest published endpoint September 2022; current data absent and archive download audit form requires identity. |

## Practical continuation

1. Prioritise the Elands Bay archive for historical comparison once the declared download form can be completed with authorised real details. Preserve CC BY-SA provenance and measurement units; model archives must match its dates.
2. Keep a separate regional agriculture evaluation option, gated on redistribution terms, timestamp interpretation and measurement units; never silently apply these offsets to the ten towns.
3. Recheck the official SAAQIS public access path for municipal meteorology after service access is resolved. It is the most plausible future local route for Velddrif, Vredenburg and Saldanha, but no operational claim is justified today.
4. Monitor the unrestricted NOAA/CWOP registry for genuinely nearby additions. A future openly opted-in owner feed could also solve local gaps, but contacting owners was outside this task and was not done.

These results justify continued comparison and source research, not replacing the site's MET default or extending the calibrated model's geographical claims.
