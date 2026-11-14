const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { boundary, validate } = require('./coastal_boundary.cjs');
const times = ['2026-01-15T00:00:00Z', '2026-06-15T12:34:56Z', '2026-11-14T22:05:00Z'];
// Captured directly from current site predictorAt on 2026-11-14, including
// summer/winter, seconds, gauge endpoints, Langebaan, Velddrif and Berg reach.
const fixtures = [
  [-33.9, [[1.2888043555554163,1.2898369675942607],[1.569016603048642,1.2766411043327621],[0.5674474784764698,0.6043828315904824]]],
  [-33.02, [[1.2980756215069207,1.3038930474483847],[1.5684277973483094,1.2867338273177324],[0.5807896617945354,0.6098351711392314]]],
  [-32.7900528, [[1.3059096158801478,1.3126650913450755],[1.5799432915308476,1.2967517830856137],[0.5850393845064481,0.6143524798892364]]],
  [-33.09, [[1.2973381344425958,1.3027749501872612],[1.5684746341653808,1.2859309970802908],[0.5797283517578711,0.6094014623114898]]],
  [-32.81, [[1.3052238361497963,1.3118982967997825],[1.5789309771889393,1.2958742933268521],[0.5846698590126118,0.6139570712122606]]],
  [-29.28, [[1.4264812110671103,1.4474804517512445],[1.757925156016712,1.451029221294363],[0.6500081729996944,0.683871966721153]]],
];
test('boundary reproduces current site fixture levels to floating point tolerance', async () => {
  for (const [latitude, expected] of fixtures) {
    const got = await boundary({ latitude, times, minutes: [0, 55] });
    got.levels.forEach((row, i) => row.forEach((v, j) => assert.ok(Math.abs(v - expected[i][j]) < 1e-12, `${latitude} ${times[i]} lag${j}`)));
  }
});
test('positive lag means earlier coastal time under native time quantization', async () => {
  const t = '2026-06-15T12:34:56Z';
  const earlier = new Date(Date.parse(t) - 55 * 60000).toISOString();
  const got = await boundary({ latitude: -32.7900528, times: [t, earlier], minutes: [0, 55] });
  assert.equal(got.levels[0][1], got.levels[1][0]);
});
test('five-minute left lookup reproduces native 0/55-minute boundary within 0.001m across a year', async (t) => {
  const sample = [];
  for (let day = 0; day < 365; day++) {
    // Spread exact scene-like times across hours, five-minute bins and seasons.
    const ms = Date.parse('2026-01-01T00:00:00Z') + day * 86400000 + (day % 24) * 3600000 + (day % 12) * 300000 + 151000;
    sample.push(new Date(ms).toISOString());
  }
  const lower = sample.map((t) => new Date(Math.floor(Date.parse(t) / 300000) * 300000).toISOString());
  const upper = lower.map((t) => new Date(Date.parse(t) + 300000).toISOString());
  const input = { latitude: -32.7900528, minutes: [0,55] };
  const actual = await boundary({ ...input, times: sample });
  const left = await boundary({ ...input, times: lower });
  const right = await boundary({ ...input, times: upper });
  const leftError = Math.max(...actual.levels.flatMap((row, i) => row.map((v,j) => Math.abs(v - left.levels[i][j]))));
  assert.ok(leftError < 0.001, `left lookup maximum error ${leftError}`);
  // Guard against accidentally introducing linear interpolation of a staircase.
  const error = Math.max(...actual.levels.flatMap((row, i) => row.map((v,j) => Math.abs(v - (left.levels[i][j] + (right.levels[i][j] - left.levels[i][j]) * 151/300)))));
  assert.ok(error > 0.001, 'native quantization must not be treated as a continuous interpolant');
  t.diagnostic(`365 scene times, two lags: max left lookup error ${leftError}m; max linear interpolation error ${error}m`);
});
test('rejects malformed coordinates, times, calendar normalization and unbounded lags', () => {
  const valid = { latitude: -33, times, minutes: [0, 55] };
  for (const latitude of [null, '33', NaN, Infinity, -91, 91]) assert.throws(() => validate({ ...valid, latitude }));
  for (const times of [[], ['2026-02-30T00:00:00Z'], ['2026-01-01'], ['2026-01-01T02:00:00+02:00'], ['2026-01-01T24:00:00Z'], ['bad'], ['2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z']]) assert.throws(() => validate({ ...valid, times }));
  for (const minutes of [[], [-1], [121], [0.5], ['55'], [0, 0]]) assert.throws(() => validate({ ...valid, minutes }));
});
test('retains all three TICON-4 station licences and 50 constituents', () => {
  const data = require('./data/tide-stations.json');
  assert.equal(data.stations.length, 3);
  for (const s of data.stations) {
    assert.equal(s.constituents.length, 50);
    assert.equal(s.license, 'cc-by-4.0');
    assert.match(s.attribution, /109129/);
  }
});
test('frozen manifest matches exact constants, helper, training CSV and installed predictor', () => {
  const model = require('./research/berg-model.json');
  const sha = (file) => createHash('sha256').update(fs.readFileSync(path.join(__dirname, file))).digest('hex');
  assert.equal(sha('data/tide-stations.json'), model.boundary_sha256, 'constants changed');
  assert.equal(sha('coastal_boundary.cjs'), model.boundary_implementation_sha256, 'boundary implementation changed');
  assert.equal(sha('research/berg-swot.csv'), model.training_source_sha256, 'training evidence changed');
  const installed = JSON.parse(fs.readFileSync(path.join(__dirname, 'node_modules/@neaps/tide-predictor/package.json'), 'utf8'));
  assert.equal(`${installed.name}@${installed.version}`, model.boundary_predictor_version);
  const declared = require('./package.json').dependencies['@neaps/tide-predictor'];
  const locked = require('./package-lock.json').packages['node_modules/@neaps/tide-predictor'].version;
  assert.equal(installed.version, declared, 'manifest requires an exact dependency pin');
  assert.equal(installed.version, locked, 'installed dependency differs from lock');
});
// Optional independent live-checkout regression, without a production dependency
// on the site's TypeScript test loader. Set SITE_MOBILE to its absolute mobile dir.
test('live site byte-identical constants and numerical parity', { skip: !process.env.SITE_MOBILE }, async () => {
  const mobile = path.resolve(process.env.SITE_MOBILE);
  const source = fs.readFileSync(path.join(mobile, 'src/data/tide-stations.json'));
  assert.equal(createHash('sha256').update(source).digest('hex'), createHash('sha256').update(fs.readFileSync(path.join(__dirname, 'data/tide-stations.json'))).digest('hex'));
  const site = require(path.join(mobile, 'tests/helpers/load-ts.cjs'))('lib/tides.ts');
  for (const [latitude] of fixtures) {
    const { predictor, from } = site.predictorAt(latitude);
    const got = await boundary({ latitude, times, minutes: [0, 55] });
    assert.deepEqual(got.from, from);
    assert.deepEqual(got.levels, times.map((t) => [0,55].map((lag) => predictor.getWaterLevelAtTime({ time: new Date(Date.parse(t) - lag * 60000) }).level)));
  }
});
