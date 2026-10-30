#!/usr/bin/env node
// One-off: build narration/take-hashes.json for recordings made before the registry
// existed, using the coordinator's saved pre-CPU snapshot to attribute each take's
// device. It only RECORDS what is on disk — it never re-records and never edits keys.
import {createHash} from 'node:crypto';
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const OUT = process.env.GUIDE_VIDEO_OUT ?? resolve(dirname(fileURLToPath(import.meta.url)), '../../../output/guide-video');
const root = join(OUT, 'narration');
const timings = JSON.parse(readFileSync(join(root, 'timings.json'), 'utf8'));
const snapPath = join(OUT, 'qa/mps-takes-before-cpu.json');

// Everything listed in the pre-CPU snapshot was rendered on MPS; the rest on CPU.
let mps = new Set();
if (existsSync(snapPath)) {
  const snap = JSON.parse(readFileSync(snapPath, 'utf8'));
  const names = JSON.stringify(snap).match(/[a-z]+-\d{2}\.wav/g) ?? [];
  mps = new Set(names);
}

const registry = {};
for (const video of Object.values(timings.videos))
  for (const ch of video.chapters)
    for (const l of ch.lines) {
      const file = join(OUT, l.file);
      const name = file.split('/').pop();
      registry[name] = {
        sha256: createHash('sha256').update(readFileSync(file)).digest('hex'),
        device: mps.has(name) ? 'mps' : 'cpu',
        seconds: l.seconds,
      };
    }
writeFileSync(join(root, 'take-hashes.json'), JSON.stringify(registry, null, 1));
const counts = Object.values(registry).reduce((a, e) => ({...a, [e.device]: (a[e.device] ?? 0) + 1}), {});
console.log(`registered ${Object.keys(registry).length} takes:`, counts);
