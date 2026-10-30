import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {MANIFEST_NAME, MEDIA_FILES, buildManifest} from './manifest.mjs';
import {buildTimeline, chapterMarkers} from './timeline.mjs';

const read = (p) => JSON.parse(readFileSync(new URL(p, import.meta.url), 'utf8'));
const narration = read('../narration.json');
const catalog = read('../../../web/src/guide/catalog.json');

const VERSION = 'guide-20261030-v1';
const sha = (n) => String(n).repeat(64).slice(0, 64);
const hashes = Object.fromEntries(MEDIA_FILES.map((f, i) => [f, sha(i % 10)]));
const chapters = chapterMarkers(
  buildTimeline({
    title: 'x',
    chapters: narration.videos.walkthrough.chapters.map((c) => ({
      ...c,
      lines: c.lines.map((text, i) => ({file: `${c.id}-${i}.wav`, text, seconds: 4})),
    })),
  }),
);
const base = {version: VERSION, chapters, durations: {intro: 78.2, walkthrough: 402.4}, hashes};

test('manifest paths are versioned and point at the rendered files', () => {
  const m = buildManifest(base);
  assert.equal(m.version, VERSION);
  for (const film of [m.intro, m.walkthrough]) {
    assert.match(film.src, new RegExp(`^/guide-media/${VERSION}/\\w+\\.mp4$`));
    assert.equal(film.poster, `/guide-media/${VERSION}/poster.jpg`);
    assert.match(film.captions, new RegExp(`^/guide-media/${VERSION}/\\w+\\.vtt$`));
    assert.ok(film.duration > 0);
  }
  assert.equal(m.intro.src, `/guide-media/${VERSION}/intro.mp4`);
});

test('manifest chapters are the catalog chapter ids with increasing starts', () => {
  const m = buildManifest(base);
  assert.deepEqual(m.chapters.map((c) => c.id), catalog.features.map((f) => f.chapter));
  for (const c of m.chapters) assert.ok(typeof c.title === 'string' && c.title.length > 0);
  for (let i = 1; i < m.chapters.length; i++) assert.ok(m.chapters[i].start > m.chapters[i - 1].start);
  assert.ok(m.chapters.at(-1).start < m.walkthrough.duration);
});

test('every rendered file is hashed and the manifest never hashes itself', () => {
  const m = buildManifest(base);
  assert.deepEqual(m.files.map((f) => f.name), MEDIA_FILES);
  for (const f of m.files) assert.match(f.sha256, /^[0-9a-f]{64}$/);
  assert.ok(!m.files.some((f) => f.name === MANIFEST_NAME));
  assert.throws(() => buildManifest({...base, hashes: {...hashes, [MANIFEST_NAME]: sha(1)}}), /must not hash itself/);
});

test('an unrendered file, a zero duration or a bad version is refused', () => {
  const {['poster.jpg']: _drop, ...partial} = hashes;
  assert.throws(() => buildManifest({...base, hashes: partial}), /unhashed files: poster\.jpg/);
  assert.throws(() => buildManifest({...base, durations: {intro: 0, walkthrough: 1}}), /no measured duration/);
  assert.throws(() => buildManifest({...base, version: 'latest'}), /bad media version/);
});
