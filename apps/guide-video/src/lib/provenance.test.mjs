import assert from 'node:assert/strict';
import test from 'node:test';
import {KEY_FIELD_COUNT, takeKey, verifyProvenance} from './provenance.mjs';

const ANCHOR_SHA = 'a'.repeat(64);
const MODEL_REV = '0bac006ac1b18c94';
const script = {
  voiceVersion: 'drishti-narrator-v1',
  voice: {id: 'narrator-a', description: 'A warm adult narrator', anchorLine: 'Hello there.',
          seed: 1309, anchorFrom: 'audition/narrator-a/anchor.wav', style: 'calm and clear'},
  videos: {intro: {chapters: [{id: 'what', lines: ['One sentence.', 'Another sentence.']}]}},
};
const FIELDS = ['narrator-a', 'drishti-narrator-v1', '1309', '10', '2.0', MODEL_REV, ANCHOR_SHA];
const anchor = {id: 'narrator-a', voiceVersion: 'drishti-narrator-v1', description: 'A warm adult narrator',
  anchorLine: 'Hello there.', seed: 1309, anchorFrom: 'audition/narrator-a/anchor.wav',
  sha256: ANCHOR_SHA, modelRevision: MODEL_REV, keyFields: FIELDS};
const timings = {anchorSha256: ANCHOR_SHA, modelRevision: MODEL_REV, steps: 10, cfgValue: 2.0, keyFields: FIELDS};
const ledger = Object.fromEntries(
  script.videos.intro.chapters[0].lines.map((t, i) => [`what-0${i}.wav`, takeKey(FIELDS, `(${script.voice.style})${t}`)]),
);
const registry = {'what-00.wav': {sha256: 'c'.repeat(64), device: 'mps', seconds: 3.8},
                  'what-01.wav': {sha256: 'd'.repeat(64), device: 'cpu', seconds: 4.2}};
const wavHashes = {'what-00.wav': 'c'.repeat(64), 'what-01.wav': 'd'.repeat(64)};
const good = () => ({script, timings, anchor, ledger, anchorSha: ANCHOR_SHA, registry, wavHashes});

test('a consistent narration passes', () => {
  assert.deepEqual(verifyProvenance(good()), []);
  assert.equal(FIELDS.length, KEY_FIELD_COUNT);
});

test('replacing anchor.wav fails even when every recorded hash still agrees', () => {
  // This is the whole point: anchor.json and timings.json agree with each other, and
  // the ledger keys are untouched — only the bytes on disk changed.
  const problems = verifyProvenance({...good(), anchorSha: 'b'.repeat(64)});
  assert.ok(problems.some((p) => /the voice on disk was replaced/.test(p)), problems.join('; '));
  assert.ok(problems.some((p) => /measured against/.test(p)));
});

test('a missing anchor.wav is refused rather than skipped', () => {
  const problems = verifyProvenance({...good(), anchorSha: null});
  assert.ok(problems.some((p) => /anchor\.wav is missing/.test(p)), problems.join('; '));
});

test('keyFields is validated against its own inputs before keys are recomputed', () => {
  // A tampered keyFields that still hashes the ledger consistently must not be trusted.
  const tampered = ['narrator-z', ...FIELDS.slice(1)];
  const fakeLedger = Object.fromEntries(
    script.videos.intro.chapters[0].lines.map((t, i) => [`what-0${i}.wav`, takeKey(tampered, `(${script.voice.style})${t}`)]),
  );
  const problems = verifyProvenance({...good(), timings: {...timings, keyFields: tampered}, ledger: fakeLedger});
  assert.ok(problems.some((p) => /keyFields voice id/.test(p)), problems.join('; '));
  // Anchor-sha and model-revision substitution is caught too.
  for (const [i, re] of [[5, /keyFields model revision/], [6, /keyFields anchor sha/]]) {
    const f = [...FIELDS];
    f[i] = 'deadbeef';
    assert.ok(verifyProvenance({...good(), timings: {...timings, keyFields: f}}).some((p) => re.test(p)));
  }
  assert.ok(verifyProvenance({...good(), timings: {...timings, keyFields: FIELDS.slice(0, 5)}})
    .some((p) => /expected 7/.test(p)));
});

test('steps and cfg are compared numerically, not as strings', () => {
  assert.deepEqual(verifyProvenance({...good(), timings: {...timings, cfgValue: 2}}), []);
  assert.ok(verifyProvenance({...good(), timings: {...timings, steps: 6}}).some((p) => /keyFields steps/.test(p)));
  assert.ok(verifyProvenance({...good(), timings: {...timings, cfgValue: 3}}).some((p) => /keyFields cfg/.test(p)));
});

test('a changed voice declaration fails even though every word is identical', () => {
  for (const [field, value] of [['seed', 42], ['description', 'A different voice'], ['anchorLine', 'Other.']]) {
    const problems = verifyProvenance({...good(), script: {...script, voice: {...script.voice, [field]: value}}});
    assert.ok(problems.some((p) => p.includes(`anchor.json ${field}`)), `${field}: ${problems.join('; ')}`);
  }
  const bumped = {...script, voiceVersion: 'v2'};
  assert.ok(verifyProvenance({...good(), script: bumped}).some((p) => /voiceVersion/.test(p)));
});

test('an edited sentence or a missing take is caught per file', () => {
  const edited = {...script, videos: {intro: {chapters: [{id: 'what', lines: ['One sentence.', 'Edited sentence.']}]}}};
  assert.ok(verifyProvenance({...good(), script: edited}).some((p) => /what-01\.wav: ledger key/.test(p)));
  const {['what-01.wav']: _gone, ...partial} = ledger;
  assert.ok(verifyProvenance({...good(), ledger: partial}).some((p) => /what-01\.wav: no ledger key/.test(p)));
});

test('a replaced take WAV is caught even though its key is untouched', () => {
  // The key proves what was asked for; only the registry proves which bytes answered.
  const swapped = {...wavHashes, 'what-01.wav': 'e'.repeat(64)};
  const problems = verifyProvenance({...good(), wavHashes: swapped});
  assert.equal(problems.length, 1, problems.join('; '));
  assert.match(problems[0], /what-01\.wav: staged audio .* != registered .* — the recording was replaced/);
});

test('an unregistered or unhashed take is refused rather than trusted', () => {
  const {['what-01.wav']: _drop, ...partial} = registry;
  assert.ok(verifyProvenance({...good(), registry: partial})
    .some((p) => /what-01\.wav: no recorded sha256/.test(p)));
  const {['what-01.wav']: _gone, ...unhashed} = wavHashes;
  assert.ok(verifyProvenance({...good(), wavHashes: unhashed})
    .some((p) => /what-01\.wav: the staged WAV was not hashed/.test(p)));
});

test('byte checks are skipped only when no registry is supplied at all', () => {
  // Keeps the registry a separate, additive artefact: its absence is handled by the
  // caller (render.mjs refuses to render), not silently waved through here.
  assert.deepEqual(verifyProvenance({...good(), registry: undefined, wavHashes: undefined}), []);
});
