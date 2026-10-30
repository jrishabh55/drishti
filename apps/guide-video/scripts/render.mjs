#!/usr/bin/env node
// Drives the two renders. Everything downstream of the voice is derived from the
// *measured* WAV durations in narration/timings.json — nothing is estimated.
//
//   node scripts/render.mjs frames   sample stills for visual inspection
//   node scripts/render.mjs sidecars captions + chapters only (fast)
//   node scripts/render.mjs all      sidecars + both 1080p MP4s + poster
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync, existsSync, linkSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {toVtt} from '../src/lib/captions.mjs';
import {verifyProvenance} from '../src/lib/provenance.mjs';
import {buildTimeline, chapterMarkers} from '../src/lib/timeline.mjs';

const PKG = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = process.env.GUIDE_VIDEO_OUT ?? resolve(PKG, '../../output/guide-video');
export const VERSION = process.env.GUIDE_MEDIA_VERSION ?? 'guide-20261030-v1';
const DIR = join(OUT, VERSION);
const ENTRY = 'src/index.ts';

const timingsPath = join(OUT, 'narration/timings.json');
if (!existsSync(timingsPath)) {
  console.error(`Missing ${timingsPath}. Run \`npm run narrate\` first — the timeline needs real audio durations.`);
  process.exit(1);
}
const timings = JSON.parse(readFileSync(timingsPath, 'utf8'));


const script = JSON.parse(readFileSync(join(PKG, 'src/narration.json'), 'utf8'));

function assertVoiceProvenance() {
  const anchorPath = join(OUT, 'narration/anchor.json');
  const ledgerPath = join(OUT, 'narration/takes.json');
  const wavPath = join(OUT, 'narration/anchor.wav');
  if (!existsSync(anchorPath) || !existsSync(ledgerPath)) {
    console.error('missing narration/anchor.json or narration/takes.json — run `npm run narrate`');
    process.exit(1);
  }
  const anchor = JSON.parse(readFileSync(anchorPath, 'utf8'));
  const anchorSha = existsSync(wavPath) ? createHash('sha256').update(readFileSync(wavPath)).digest('hex') : null;
  const ledger = JSON.parse(readFileSync(ledgerPath, 'utf8'));
  const registryPath = join(OUT, 'narration/take-hashes.json');
  const registry = existsSync(registryPath) ? JSON.parse(readFileSync(registryPath, 'utf8')) : null;
  if (!registry) {
    console.error('missing narration/take-hashes.json — run `node scripts/register-takes.mjs`');
    process.exit(1);
  }
  // Hash the bytes that will actually be bundled, not the ones the ledger remembers.
  const wavHashes = {};
  for (const video of Object.values(timings.videos))
    for (const ch of video.chapters)
      for (const l of ch.lines) {
        const f = join(OUT, l.file);
        const name = l.file.split('/').pop();
        wavHashes[name] = existsSync(f) ? createHash('sha256').update(readFileSync(f)).digest('hex') : null;
      }
  const problems = verifyProvenance({script, timings, anchor, ledger, anchorSha, registry, wavHashes});
  if (problems.length) {
    console.error('narration provenance does not match the script:\n  ' + problems.join('\n  '));
    process.exit(1);
  }
  console.log(`voice ${script.voice.id}/${script.voiceVersion} · anchor.wav ${anchorSha.slice(0, 12)}… (hashed, matches) · model ${anchor.modelRevision} · ${Object.keys(ledger).length} takes verified by key AND bytes · device ${timings.device}`);
}

/**
 * Refuse to render audio that no longer matches the script. Without this, correcting a
 * spoken claim in narration.json and rendering before re-running the engine would ship
 * the old words and the old captions with no warning at all.
 */
function assertTimingsMatchScript() {
  const problems = [];
  for (const [name, spec] of Object.entries(script.videos)) {
    const measured = timings.videos?.[name];
    if (!measured) {
      problems.push(`${name}: missing from timings.json`);
      continue;
    }
    const ids = spec.chapters.map((c) => c.id).join(',');
    const got = measured.chapters.map((c) => c.id).join(',');
    if (ids !== got) problems.push(`${name}: chapters are [${got}], script says [${ids}]`);
    for (const ch of spec.chapters) {
      const m = measured.chapters.find((c) => c.id === ch.id);
      if (!m) continue;
      if (m.lines.length !== ch.lines.length)
        problems.push(`${name}/${ch.id}: ${m.lines.length} takes, script has ${ch.lines.length} lines`);
      ch.lines.forEach((text, i) => {
        if (m.lines[i] && m.lines[i].text !== text)
          problems.push(`${name}/${ch.id}[${i}]: narrated "${m.lines[i].text}" but script says "${text}"`);
      });
    }
  }
  if (problems.length) {
    console.error('narration out of date — run `npm run narrate`:\n  ' + problems.join('\n  '));
    process.exit(1);
  }
}
assertTimingsMatchScript();
assertVoiceProvenance();

// Durations come from timings.json (measured); titles come from narration.json (the
// script), so re-titling a film or a chapter never needs the audio regenerated.
const titled = (name) => ({
  ...timings.videos[name],
  title: script.videos[name].title,
  chapters: timings.videos[name].chapters.map((c) => ({
    ...c,
    title: script.videos[name].chapters.find((s) => s.id === c.id).title,
  })),
});
const TIMELINES = {intro: buildTimeline(titled('intro')), walkthrough: buildTimeline(titled('walkthrough'))};

// Root.tsx imports this and burns the chapter badges from it, so it must carry the
// SCRIPT titles — the same ones chapters.json and the manifest use.
writeFileSync(join(PKG, 'src/timings.generated.json'),
  JSON.stringify({...timings, videos: {intro: titled('intro'), walkthrough: titled('walkthrough')}}));
const COMP = {intro: 'Intro', walkthrough: 'Walkthrough'};

// Only the narration is exposed to the bundle. Pointing --public-dir at the whole
// output tree let every render copy the auditions, the frames and earlier MP4s.
const PUBLIC = join(OUT, 'render-public');
function stageNarration() {
  mkdirSync(join(PUBLIC, 'narration'), {recursive: true});
  for (const [name, t] of Object.entries(TIMELINES))
    for (const ch of t.chapters)
      for (const l of ch.lines) {
        const dest = join(PUBLIC, l.file);
        mkdirSync(dirname(dest), {recursive: true});
        if (existsSync(dest)) rmSync(dest);
        // Hard link: no copy, no extra disk on a constrained machine.
        try {
          linkSync(join(OUT, l.file), dest);
        } catch {
          copyFileSync(join(OUT, l.file), dest);
        }
      }
}

const remotion = (...args) => {
  stageNarration();
  return execFileSync('npx', ['remotion', ...args, '--public-dir', PUBLIC], {cwd: PKG, stdio: 'inherit'});
};

const sidecars = () => {
  mkdirSync(DIR, {recursive: true});
  for (const [name, t] of Object.entries(TIMELINES)) writeFileSync(join(DIR, `${name}.vtt`), toVtt(t.captions));
  writeFileSync(
    join(DIR, 'chapters.json'),
    JSON.stringify(
      {
        version: VERSION,
        intro: chapterMarkers(TIMELINES.intro),
        walkthrough: chapterMarkers(TIMELINES.walkthrough),
      },
      null,
      1,
    ),
  );
  for (const [name, t] of Object.entries(TIMELINES))
    console.log(`${name}: ${t.durationSeconds.toFixed(2)}s (${t.durationInFrames}f) · audio ${t.audioSeconds.toFixed(2)}s · ${t.chapters.length} chapters · ${t.captions.length} cues`);
};

const frames = () => {
  for (const [name, t] of Object.entries(TIMELINES)) {
    const dir = join(OUT, 'frames', name);
    mkdirSync(dir, {recursive: true});
    // One still per chapter, a second in so the fade has settled. A single
    // --frames list reuses one bundle instead of re-bundling per still.
    const list = t.chapters.map((ch) => ch.startFrame + t.fps);
    remotion('render', ENTRY, COMP[name], dir, '--sequence', `--frames=${list.join(',')}`,
      '--image-format=png', '--image-sequence-pattern=[frame].png', '--concurrency=2');
    console.log(`${name} stills: ${t.chapters.map((ch, i) => `${ch.id}=${list[i]}.png`).join(' ')}`);
  }
};

const all = () => {
  // guide-media serves a version directory as immutable for a year, so silently
  // rewriting one would leave stale bytes in every cache that already fetched it.
  if (existsSync(join(DIR, 'manifest.json')) && process.env.GUIDE_MEDIA_FORCE !== '1') {
    console.error(`${VERSION} is already published in ${DIR}. Bump GUIDE_MEDIA_VERSION, ` +
      'or set GUIDE_MEDIA_FORCE=1 if this version was never published.');
    process.exit(1);
  }
  sidecars();
  for (const [name, t] of Object.entries(TIMELINES)) {
    remotion('render', ENTRY, COMP[name], join(DIR, `${name}.mp4`), '--concurrency=4');
    console.log(`rendered ${name}.mp4 (${t.durationInFrames} frames)`);
  }
  // Poster: first chapter, inside the lead-in before any narration starts, with the
  // caption band and chapter badge switched off so no sentence fragment is frozen in.
  remotion('still', ENTRY, 'Walkthrough', join(DIR, 'poster.jpg'), '--frame=10',
    `--props=${JSON.stringify({showBadge: false, showCaptions: false})}`);
};

const MODES = {frames, sidecars, all};

// scripts/manifest.mjs imports TIMELINES/DIR/VERSION from here, so only run a
// mode when this file is the entry point — importing it must never render.
if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv[2] ?? 'all';
  if (!MODES[mode]) {
    console.error(`unknown mode "${mode}" — use ${Object.keys(MODES).join(' | ')}`);
    process.exit(1);
  }
  MODES[mode]();
}

export {TIMELINES, DIR};
