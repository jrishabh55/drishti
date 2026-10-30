#!/usr/bin/env node
// Root.tsx imports src/timings.generated.json, which only exists after a render pass.
// On a fresh clone that breaks `tsc` and the Studio before the narration engine has
// ever run, so write an empty two-video stub when it is missing. Any render.mjs mode
// overwrites it with the real measured timings.
import {existsSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const target = resolve(dirname(fileURLToPath(import.meta.url)), '../src/timings.generated.json');
if (!existsSync(target)) {
  const empty = {title: '', chapters: []};
  writeFileSync(target, JSON.stringify({voice: '', voiceVersion: '', steps: 0, promptCache: true,
    sampleRate: 0, videos: {intro: empty, walkthrough: empty}}, null, 1));
  console.log(`wrote stub ${target} — run \`npm run narrate\` for real timings`);
}
