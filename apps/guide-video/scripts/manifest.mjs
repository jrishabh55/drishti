#!/usr/bin/env node
// Writes the media manifest LAST: the contract is built from src/lib/manifest.mjs,
// then every finished file is hashed. The manifest never hashes itself.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {MANIFEST_NAME, MEDIA_FILES, buildManifest} from '../src/lib/manifest.mjs';
import {chapterMarkers} from '../src/lib/timeline.mjs';
import {DIR, TIMELINES, VERSION} from './render.mjs';

const missing = MEDIA_FILES.filter((f) => !existsSync(join(DIR, f)));
if (missing.length) {
  console.error(`cannot write manifest, missing rendered files: ${missing.join(', ')}`);
  process.exit(1);
}

// Durations are read back out of the encoded files, not taken from the timeline.
const probe = (file) =>
  Number(
    execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', join(DIR, file)], {
      encoding: 'utf8',
    }).trim(),
  );

const hashes = Object.fromEntries(
  MEDIA_FILES.map((name) => [name, createHash('sha256').update(readFileSync(join(DIR, name))).digest('hex')]),
);

const manifest = buildManifest({
  version: VERSION,
  chapters: chapterMarkers(TIMELINES.walkthrough),
  durations: {intro: probe('intro.mp4'), walkthrough: probe('walkthrough.mp4')},
  hashes,
});

writeFileSync(join(DIR, MANIFEST_NAME), JSON.stringify(manifest, null, 1));
console.log(JSON.stringify(manifest, null, 1));
