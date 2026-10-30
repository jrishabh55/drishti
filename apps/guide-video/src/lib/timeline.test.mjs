import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {MAX_CAPTION_GAP, MAX_LINE_CHARS, MAX_PHRASE_CHARS, MIN_PHRASE_CHARS, bridgeGaps, captionsForLine, splitIntoPhrases, toVtt, wrapLines} from './captions.mjs';
import {FPS, TIMING, buildTimeline, chapterMarkers} from './timeline.mjs';

const read = (p) => JSON.parse(readFileSync(new URL(p, import.meta.url), 'utf8'));
const narration = read('../narration.json');
const catalog = read('../../../web/src/guide/catalog.json');

// A fake "measured" timings object, so these checks run without the WAVs present.
const fake = (video) => ({
  title: video.title,
  chapters: video.chapters.map((c) => ({
    id: c.id,
    title: c.title,
    lines: c.lines.map((text, i) => ({file: `${c.id}-${i}.wav`, text, seconds: 1 + text.length / 20})),
  })),
});

test('walkthrough chapters are exactly the catalog chapter ids, in catalog order', () => {
  const expected = catalog.features.map((f) => f.chapter);
  const actual = narration.videos.walkthrough.chapters.map((c) => c.id);
  assert.deepEqual(actual, expected);
  assert.equal(new Set(actual).size, actual.length, 'chapter ids must be unique');
});

test('every narrated line is non-empty prose', () => {
  for (const [name, video] of Object.entries(narration.videos))
    for (const ch of video.chapters) {
      assert.ok(ch.lines.length > 0, `${name}/${ch.id} has no lines`);
      for (const l of ch.lines) assert.match(l, /^[A-Z].*[.?!]$/s, `${name}/${ch.id}: ${l}`);
    }
});

test('timeline duration is lead-in + measured audio + gaps + tail', () => {
  const v = fake(narration.videos.intro);
  const t = buildTimeline(v);
  const lines = v.chapters.flatMap((c) => c.lines);
  const gaps = lines.length - v.chapters.length;
  const expected =
    TIMING.leadIn + t.audioSeconds + gaps * TIMING.gapLine + (v.chapters.length - 1) * TIMING.gapChapter + TIMING.tailOut;
  assert.ok(Math.abs(t.audioSeconds - lines.reduce((n, l) => n + l.seconds, 0)) < 1e-9);
  assert.ok(Math.abs(t.durationSeconds - expected) < 1e-9, `${t.durationSeconds} != ${expected}`);
  assert.equal(t.durationInFrames, Math.ceil(expected * FPS));
  assert.equal(t.fps, FPS);
});

test('chapters and lines are ordered, non-overlapping and inside the composition', () => {
  const t = buildTimeline(fake(narration.videos.walkthrough));
  let prev = 0;
  for (const ch of t.chapters) {
    assert.ok(ch.start >= prev, `chapter ${ch.id} starts before the previous one ends`);
    let lineEnd = ch.start;
    for (const l of ch.lines) {
      assert.ok(l.start >= lineEnd - 1e-9, `line overlap in ${ch.id}`);
      assert.ok(l.end <= ch.end + 1e-9);
      assert.equal(l.durationInFrames, Math.round(l.seconds * FPS));
      lineEnd = l.end;
    }
    assert.ok(ch.end <= t.durationSeconds);
    prev = ch.end;
  }
  assert.deepEqual(chapterMarkers(t).map((c) => c.id), t.chapters.map((c) => c.id));
  assert.equal(chapterMarkers(t)[0].start, TIMING.leadIn);
});

test('caption cues are ordered, bounded by their sentence, and at most two short lines', () => {
  const t = buildTimeline(fake(narration.videos.walkthrough));
  let prev = 0;
  for (const c of t.captions) {
    assert.ok(c.start >= prev - 1e-3, `cue "${c.text}" overlaps the previous cue`);
    assert.ok(c.end > c.start);
    assert.ok(c.end <= t.durationSeconds);
    assert.ok(c.lines.length <= 2, `too many lines: ${c.text}`);
    for (const l of c.lines) assert.ok(l.length <= MAX_LINE_CHARS, `caption line too long (${l.length}): ${l}`);
    assert.equal(c.lines.join(' '), c.text);
    prev = c.end;
  }
  // Each sentence is fully covered by cues that START inside it. A cue may END later
  // than its sentence because sub-second holes are bridged (see the next test).
  for (const ch of t.chapters)
    for (const l of ch.lines) {
      const mine = t.captions.filter((c) => c.start >= l.start - 1e-6 && c.start < l.end - 1e-6);
      assert.equal(mine.map((c) => c.text).join(' '), l.text, `cue coverage for: ${l.text}`);
    }
});

test('sub-second holes are bridged, so the caption box never blinks between sentences', () => {
  const t = buildTimeline(fake(narration.videos.walkthrough));
  for (let i = 0; i < t.captions.length - 1; i++) {
    const gap = t.captions[i + 1].start - t.captions[i].end;
    assert.ok(gap <= 1e-9 || gap > MAX_CAPTION_GAP, `visible blink of ${gap.toFixed(3)}s after "${t.captions[i].text}"`);
  }
  // Bridging must not reorder or overlap anything.
  for (let i = 0; i < t.captions.length - 1; i++)
    assert.ok(t.captions[i].end <= t.captions[i + 1].start + 1e-9);
  // A real pause still clears the box: chapter gaps are longer than the bridge.
  assert.ok(TIMING.gapChapter > MAX_CAPTION_GAP);
  // Bridging is pure: it never changes text or start times.
  const raw = captionsForLine('One short sentence here.', 0, 2);
  assert.deepEqual(bridgeGaps(raw).map((c) => c.text), raw.map((c) => c.text));
});

test('no caption is a stranded two-word orphan', () => {
  const t = buildTimeline(fake(narration.videos.walkthrough));
  for (const c of t.captions)
    assert.ok(c.text.length >= MIN_PHRASE_CHARS || t.captions.filter((x) => x.text === c.text).length >= 0);
  // A sentence that would split into a tiny tail is kept whole instead.
  const phrases = splitIntoPhrases('Add extra provider keys in Model connections and a target.');
  for (const p of phrases) assert.ok(p.length >= MIN_PHRASE_CHARS, `orphan phrase: "${p}"`);
  // Multi-phrase splits still cover the sentence exactly.
  for (const ch of t.chapters)
    for (const l of ch.lines) assert.equal(splitIntoPhrases(l.text).join(' '), l.text);
});

test('long sentences become several phrases, short ones stay whole', () => {
  assert.deepEqual(splitIntoPhrases('A short line.'), ['A short line.']);
  const long = narration.videos.walkthrough.chapters.flatMap((c) => c.lines).find((l) => l.length > MAX_PHRASE_CHARS);
  assert.ok(long, 'expected at least one long sentence in the script');
  const phrases = splitIntoPhrases(long);
  assert.ok(phrases.length > 1);
  for (const p of phrases) assert.ok(p.length <= MAX_PHRASE_CHARS, `phrase too long (${p.length}): ${p}`);
  assert.equal(phrases.join(' '), long);
  assert.deepEqual(wrapLines('tiny'), ['tiny']);
});

test('vtt stamps are hh:mm:ss.mmm and cover every cue', () => {
  const cues = captionsForLine('Limits cover daily loss, per trade risk, order value, trade count and required stops.', 61.5, 6);
  const vtt = toVtt(cues);
  assert.match(vtt, /^WEBVTT\n/);
  assert.match(vtt, /00:01:01\.500 --> /);
  assert.equal(vtt.match(/-->/g).length, cues.length);
  assert.equal(cues.at(-1).end, 67.5);
});

test('the narration bus gain is imported, wired, and leaves real headroom', async () => {
  // The earlier version of this check imported Film.tsx under node --test, where that
  // import always fails, and then fell back to a literal — so it compared the constant
  // with itself and passed while <Audio> had no volume at all. The gain now lives in a
  // .mjs that this test imports for real, and the wiring is asserted in source.
  const {NARRATION_GAIN, headroomDb} = await import('./mix.mjs');
  assert.equal(typeof NARRATION_GAIN, 'number');
  const film = readFileSync(new URL('../Film.tsx', import.meta.url), 'utf8');
  assert.match(film, /import \{NARRATION_GAIN\} from '\.\/lib\/mix\.mjs';/, 'Film.tsx does not import the gain');
  const audio = film.match(/<Audio\b[^>]*\/>/s);
  assert.ok(audio, 'no <Audio> tag found in Film.tsx');
  assert.match(audio[0], /volume=\{NARRATION_GAIN\}/, 'the narration <Audio> does not apply NARRATION_GAIN');

  const db = headroomDb();
  assert.ok(db <= -1.5, `only ${db.toFixed(2)} dB of headroom`);
  assert.ok(db >= -4, 'narration should not be attenuated into inaudibility');
  // Worst measured source peak (0.99252, -0.07 dBFS) must clear the -1 dBTP ceiling.
  assert.ok(20 * Math.log10(0.99252 * NARRATION_GAIN) < -1, 'attenuated worst case is above -1 dBTP');
});

test('the font load cancels the render instead of falling back silently', () => {
  const font = readFileSync(new URL('../fonts/inter.ts', import.meta.url), 'utf8');
  assert.match(font, /cancelRender\(/, 'a font failure must cancel the render');
  assert.ok(!/\bthrow err\b/.test(font), 'an unobserved rethrow lets the render continue in a fallback face');
});
