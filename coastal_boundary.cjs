// Frozen coastal boundary for the Berg research trial, not a local estuary model.
// Constants: TICON-4 CC BY 4.0; full source/attribution retained in the data file.
// Algorithm copied from the site's constantsAt/predictorAt (2026-11-14).
// Phase is in degrees as provided to Neaps; a positive lag samples time - lag.
// IMPORTANT: Neaps 0.11.0 getWaterLevelAtTime floors its timeline to 10 UTC
// minutes. Keep that behavior because the frozen transfer was fitted against it.
// A 5-minute freeze grid reproduces lags 0/55 via LEFT lookup, not interpolation.
const DATA = require('./data/tide-stations.json');

function constantsAt(lat) {
  const sorted = [...DATA.stations].sort((a, b) => a.lat - b.lat);
  const first = sorted[0], last = sorted[sorted.length - 1];
  const name = (s) => s.name === 'Saldahna Bay' ? 'Saldanha Bay' : s.name;
  const at = (s) => ({ constituents: s.constituents, msl: s.msl, latDatum: s.lat_datum, from: [name(s)] });
  if (lat <= first.lat) return at(first);
  if (lat >= last.lat) return at(last);
  const hi = sorted.findIndex((s) => s.lat >= lat);
  const a = sorted[hi - 1], b = sorted[hi];
  const f = (lat - a.lat) / (b.lat - a.lat);
  const bByName = new Map(b.constituents.map((c) => [c.name, c]));
  const constituents = [];
  const rad = (d) => (d * Math.PI) / 180;
  const deg = (r) => (r * 180) / Math.PI;
  for (const ca of a.constituents) {
    const cb = bByName.get(ca.name);
    if (!cb) continue;
    const x = (1 - f) * ca.amplitude * Math.cos(rad(ca.phase)) + f * cb.amplitude * Math.cos(rad(cb.phase));
    const y = (1 - f) * ca.amplitude * Math.sin(rad(ca.phase)) + f * cb.amplitude * Math.sin(rad(cb.phase));
    constituents.push({ name: ca.name, amplitude: Math.hypot(x, y), phase: (deg(Math.atan2(y, x)) + 360) % 360 });
  }
  const blend = (p, q) => (1 - f) * p + f * q;
  return { constituents, msl: blend(a.msl, b.msl), latDatum: blend(a.lat_datum, b.lat_datum), from: [name(a), name(b)] };
}

function validate(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Expected a JSON object');
  const { latitude, times, minutes } = input;
  if (typeof latitude !== 'number' || !Number.isFinite(latitude) || latitude < -90 || latitude > 90) throw new Error('latitude must be a finite number in [-90, 90]');
  if (!Array.isArray(times) || times.length < 1 || times.length > 20000) throw new Error('times must contain 1 to 20000 UTC instants');
  for (const t of times) {
    if (typeof t !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(t)) throw new Error('times must use ISO UTC with seconds and Z');
    const d = new Date(t);
    if (!Number.isFinite(d.getTime()) || d.toISOString() !== (t.includes('.') ? t : t.replace('Z', '.000Z'))) throw new Error('Invalid UTC calendar instant');
  }
  if (!Array.isArray(minutes) || minutes.length < 1 || minutes.length > 25 || minutes.some((v) => !Number.isInteger(v) || v < 0 || v > 120)) throw new Error('minutes must contain 1 to 25 integer lags in [0, 120]');
  if (new Set(times).size !== times.length || new Set(minutes).size !== minutes.length) throw new Error('Duplicate times or lags');
}

async function boundary(input) {
  validate(input);
  const { createTidePredictor } = await import('@neaps/tide-predictor');
  const c = constantsAt(input.latitude);
  const predictor = createTidePredictor(c.constituents, { offset: c.msl - c.latDatum });
  const levels = input.times.map((time) => input.minutes.map((lag) =>
    predictor.getWaterLevelAtTime({ time: new Date(Date.parse(time) - lag * 60000) }).level));
  if (levels.some((row) => row.some((value) => !Number.isFinite(value)))) throw new Error('Non-finite boundary result');
  return { from: c.from, levels };
}

module.exports = { boundary, constantsAt, validate };
if (require.main === module) {
  (async () => {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of process.stdin) {
      bytes += chunk.length;
      if (bytes > 1024 * 1024) throw new Error('Input exceeds 1 MiB');
      chunks.push(chunk);
    }
    process.stdout.write(JSON.stringify(await boundary(JSON.parse(Buffer.concat(chunks).toString('utf8')))) + '\n');
  })().catch((error) => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
