#!/usr/bin/env node
// Measured QC for the generated narration. No listening required: this catches
// clipped, silent, truncated or runaway takes, which is what actually goes wrong.
//
//   node scripts/audio-check.mjs [audition|narration]
import {execFileSync, spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const OUT = process.env.GUIDE_VIDEO_OUT ?? resolve(dirname(fileURLToPath(import.meta.url)), '../../../output/guide-video');
// ffmpeg's filter measurements land on stderr.
const ff = (...a) => spawnSync('ffmpeg', ['-v', 'info', '-nostats', ...a], {encoding: 'utf8'}).stderr;

const LIMITS = {peakDb: -0.5, meanDbMin: -34, meanDbMax: -12, wps: [1.6, 4.2], tailSilence: 0.05};

function measure(file, text) {
  const log = ff('-i', file, '-af', 'volumedetect,silencedetect=n=-45dB:d=0.04', '-f', 'null', '-');
  const num = (re) => Number((log.match(re) ?? [])[1]);
  const duration = Number(
    execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file], {encoding: 'utf8'}),
  );
  const ends = [...log.matchAll(/silence_start: ([\d.]+)/g)].map((m) => Number(m[1]));
  const lastSilenceStart = ends.length ? ends.at(-1) : null;
  const words = text ? text.split(/\s+/).length : null;
  const m = {
    file: file.replace(`${OUT}/`, ''),
    seconds: Math.round(duration * 1000) / 1000,
    peakDb: num(/max_volume: (-?[\d.]+) dB/),
    meanDb: num(/mean_volume: (-?[\d.]+) dB/),
    wordsPerSecond: words ? Math.round((words / duration) * 100) / 100 : null,
    // A take that ends mid-word has no trailing silence at all.
    trailingSilence: lastSilenceStart === null ? 0 : Math.round((duration - lastSilenceStart) * 1000) / 1000,
  };
  m.problems = [
    m.peakDb > LIMITS.peakDb && `clipping risk (peak ${m.peakDb} dB)`,
    m.meanDb < LIMITS.meanDbMin && `too quiet (mean ${m.meanDb} dB)`,
    m.meanDb > LIMITS.meanDbMax && `too hot (mean ${m.meanDb} dB)`,
    m.wordsPerSecond !== null && (m.wordsPerSecond < LIMITS.wps[0] || m.wordsPerSecond > LIMITS.wps[1]) &&
      `speech rate ${m.wordsPerSecond} w/s outside ${LIMITS.wps.join('–')}`,
    m.trailingSilence < LIMITS.tailSilence && 'no trailing silence — the take may be cut off',
  ].filter(Boolean);
  return m;
}

const mode = process.argv[2] ?? 'narration';
const results = [];
if (mode === 'narration') {
  const t = JSON.parse(readFileSync(join(OUT, 'narration/timings.json'), 'utf8'));
  results.push(measure(join(OUT, 'narration/anchor.wav'), null));
  for (const v of Object.values(t.videos))
    for (const ch of v.chapters) for (const l of ch.lines) results.push(measure(join(OUT, l.file), l.text));
} else {
  const a = JSON.parse(readFileSync(join(OUT, 'audition/audition.json'), 'utf8'));
  for (const [name, v] of Object.entries(a)) {
    results.push(measure(join(OUT, `audition/${name}/anchor.wav`), null));
    for (const take of v.takes) results.push(measure(join(OUT, 'audition', take.file.replace('audition/', '')), take.text));
  }
}

const bad = results.filter((r) => r.problems.length);
for (const r of bad) console.log(`FLAG ${r.file}: ${r.problems.join('; ')}`);
const stat = (k) => {
  const v = results.map((r) => r[k]).filter((n) => Number.isFinite(n));
  return {min: Math.min(...v), max: Math.max(...v), mean: Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 100) / 100};
};
console.log(
  JSON.stringify(
    {mode, files: results.length, flagged: bad.length, seconds: stat('seconds'), peakDb: stat('peakDb'), meanDb: stat('meanDb'), wordsPerSecond: stat('wordsPerSecond')},
    null,
    1,
  ),
);
// Non-zero when anything was flagged, so this can gate a render or CI. Flags are a
// signal to listen, not proof of a bad clip — report them, never pad them away.
process.exitCode = bad.length ? 1 : 0;
