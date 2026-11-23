"""Fresh SST recovery must not relabel old, malformed or land data as current."""
from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import patch

import marine

NOW = datetime(2026, 11, 23, 16, tzinfo=UTC)
CSV = b'''time,latitude,longitude,analysed_sst
UTC,degrees_north,degrees_east,degree_C
2026-11-21T12:00:00Z,-33.375,17.875,15.42
2026-11-21T12:00:00Z,-33.125,17.625,15.52
2026-11-21T12:00:00Z,-32.875,17.625,14.71
2026-11-21T12:00:00Z,-32.625,18.125,13.85
2026-11-21T12:00:00Z,-32.375,18.125,13.72
2026-11-21T12:00:00Z,-32.125,18.125,13.94
'''


def oisst(day):
    return {area: {'valid_utc': day + 'T12:00:00Z', 'temperature_c': 16.07,
                   'latitude': spec[2][0], 'longitude': spec[2][1], 'source_url': 'https://www.ncei.noaa.gov/old.nc'}
            for area, spec in marine.AREAS.items()}


class CoralTempRecoveryTests(TestCase):
    def collect(self, rows, payload=CSV, previous=None):
        with patch.object(marine, 'read_sst', return_value=({}, rows)), \
                patch.object(marine, 'read_waves', return_value=({}, None)), \
                patch.object(marine, 'fetch', return_value=(payload, {})):
            return marine.update_marine(previous, now=NOW)

    def test_expired_oisst_uses_fresh_noaa_product_with_its_own_credit_and_date(self):
        result = self.collect(oisst('2026-11-18'))
        self.assertEqual(result['refresh_errors'], [])
        self.assertEqual(len(result['towns']), 9)
        row = result['towns']['langebaan']['sst']
        self.assertEqual(row['temperature_c'], 15.52)
        self.assertEqual(row['valid_utc'], '2026-11-21T12:00:00Z')
        self.assertIn('CoralTemp', row['source_name'])
        self.assertIn('nighttime', row['source_name'])
        self.assertEqual((row['latitude'], row['longitude']), (-33.125, 17.625))

    def test_unavailable_oisst_also_recovers(self):
        with patch.object(marine, 'read_sst', side_effect=RuntimeError('unavailable')), \
                patch.object(marine, 'read_waves', return_value=({}, None)), \
                patch.object(marine, 'fetch', return_value=(CSV, {})):
            result = marine.update_marine(None, now=NOW)
        self.assertEqual(result['refresh_errors'], [])
        self.assertEqual(result['towns']['paternoster']['sst']['temperature_c'], 14.71)

    def test_bad_fallback_cannot_clear_stale_source_failure(self):
        invalid = [CSV.replace(b'degree_C', b'K'), CSV.replace(b'2026-11-21', b'2026-11-18'),
                   CSV.replace(b'2026-11-21', b'2026-11-24'), CSV.replace(b'15.52', b'NaN'),
                   CSV.replace(b'-33.125', b'-33.120'), CSV.replace(b'15.52', b'85'),
                   CSV + CSV.splitlines(keepends=True)[3], b'<html>maintenance</html>',
                   CSV.replace(b'2026-11-21T12:00:00Z,-33.125', b'2026-11-20T12:00:00Z,-33.125')]
        for blob in invalid:
            with self.subTest(blob=blob):
                result = self.collect(oisst('2026-11-18'), blob)
                self.assertIn('sst', result['refresh_errors'])
                self.assertIsNone(result['towns']['langebaan']['sst'])

    def test_three_day_old_oisst_can_be_refreshed_before_hard_expiry(self):
        result = self.collect(oisst('2026-11-20'))
        self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-21T12:00:00Z')

    def test_current_oisst_does_not_require_fallback_service(self):
        with patch.object(marine, 'read_sst', return_value=({}, oisst('2026-11-22'))), \
                patch.object(marine, 'read_waves', return_value=({}, None)), \
                patch.object(marine, 'fetch', side_effect=AssertionError('unneeded fallback request')):
            result = marine.update_marine(None, now=NOW)
        self.assertEqual(result['refresh_errors'], [])
        self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-22T12:00:00Z')

    def test_valid_oisst_survives_unavailable_or_older_fallback(self):
        for payload in (b'bad', CSV.replace(b'2026-11-21', b'2026-11-19')):
            result = self.collect(oisst('2026-11-20'), payload)
            self.assertEqual(result['refresh_errors'], [])
            self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-20T12:00:00Z')

    def test_cached_expired_oisst_still_requests_fresh_product(self):
        previous = {'towns': {slug: {'area_name': spec[0], 'sst': oisst('2026-11-18')[area]}
                             for area, spec in marine.AREAS.items() for slug in spec[3]}}
        result = self.collect(None, previous=previous)
        self.assertEqual(result['refresh_errors'], [])
        self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-21T12:00:00Z')
        failed = self.collect(None, payload=b'bad', previous=previous)
        self.assertIn('sst', failed['refresh_errors'])
        self.assertEqual(failed['towns']['langebaan']['sst']['valid_utc'], '2026-11-18T12:00:00Z')

    def test_fallback_does_not_replace_newer_retained_reading(self):
        previous = {'towns': {slug: {'area_name': spec[0], 'sst': oisst('2026-11-22')[area]}
                             for area, spec in marine.AREAS.items() for slug in spec[3]}}
        result = self.collect(oisst('2026-11-18'), previous=previous)
        self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-22T12:00:00Z')

    def test_total_outage_does_not_claim_success_from_fresh_retained_data(self):
        previous = {'towns': {slug: {'area_name': spec[0], 'sst': oisst('2026-11-22')[area]}
                             for area, spec in marine.AREAS.items() for slug in spec[3]}}
        with patch.object(marine, 'read_sst', side_effect=RuntimeError('offline')), \
                patch.object(marine, 'read_waves', return_value=({}, None)), \
                patch.object(marine, 'fetch', side_effect=TimeoutError('offline')):
            result = marine.update_marine(previous, now=NOW)
        self.assertIn('sst', result['refresh_errors'])
        self.assertEqual(result['towns']['langebaan']['sst']['valid_utc'], '2026-11-22T12:00:00Z')
