# Offshore SST recovery — 23 November 2026

## Failure

The last complete publication was 23 November at 11:02:26 UTC. From noon, the retained OISST analysis of 18 November at 12:00 UTC exceeded the existing five-day age limit. Weather, station and swell updates continued, but overall health correctly became degraded. The watchdog retried; no retry could make the upstream analysis newer.

The original NCEI endpoint, NOAA cloud mirrors, NOAA PSL and CoastWatch OISST catalogues were checked. The available OISST routes did not provide a sufficiently recent analysis. PSL's annual file ended on 18 November; the Amazon inventory ended on 16 November. CoastWatch's NRT metadata also ended on 18 November, and direct access timed out locally. This differs from the 18 November transport outage: additional mirrors of the same delayed product are insufficient.

## Independent free fallback

NOAA Coral Reef Watch [CoralTemp v3.1](https://coralreefwatch.noaa.gov/product/5km/index_5km_sst.php) is a daily 0.05-degree nighttime foundation-temperature analysis. It is a different product from OISST, not an hourly thermometer or lagoon measurement. NOAA's [OceanWatch ERDDAP dataset](https://oceanwatch.pifsc.noaa.gov/erddap/info/CRW_sst_v3_1/index.html) offers small CSV subsets with explicit coordinates, dates and units. All six existing offshore coordinates fall exactly on its grid. A live subset returned all six valid values dated 21 November at 12:00 UTC; Langebaan's existing offshore coordinate returned 15.52 degrees Celsius.

The 2002-present data are free and open; NOAA-produced Coral Reef Watch data permit unrestricted use with credit. The historical 1985-2002 OSTIA restrictions do not apply to this current-data collector. Credit NOAA Coral Reef Watch and the product's [citation guidance](https://coralreefwatch.noaa.gov/satellite/docs/recommendations_crw_citation.php). No paid API, account or additional package is used. Python 3.12, h5py 3.16.0 and ecCodes 2.50.0 remain unchanged; CSV parsing uses Python's standard library. Protocol: [ERDDAP griddap documentation](https://coastwatch.pfeg.noaa.gov/erddap/griddap/documentation.html).

OISST remains preferred when no more than three days old. When it is older or unavailable, the collector checks CoralTemp and uses it only if newer than the selected reading. The unchanged five-day hard limit still applies. Exact coordinates, Celsius units, one shared date, finite plausible values and full six-area coverage are required. Duplicate cells, missing/land values at required coordinates, mixed timestamps and future data are rejected. Source name and URL travel with each reading. Older fallback data cannot replace newer retained readings. If both sources fail, old data retain their original timestamp and refresh remains failed.

## Verification and release boundary

Nine new tests cover real parsing and collection behavior using hand-checked CSV examples. They were observed failing before implementation. The existing partial mocked OISST test was updated to satisfy the reader's already-established complete-coverage contract. All 76 Python tests pass; six Node boundary tests pass and one optional site-parity check is skipped. No tide calculations or forecast calibration change.

The private site must display the new per-reading source credit before the fallback feed is released. Legacy records retain the OISST description. The existing MET forecast remains the default and the expired Geelbek trial is not reactivated. Production confirmation must include the collector, canonical site feed and independent watchdog; a successful test is not a live recovery claim.
