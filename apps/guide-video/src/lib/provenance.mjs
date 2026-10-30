// Verifies that the narration on disk is the voice the script declares, before a
// single frame is rendered. Pure: all file reading and hashing happens in render.mjs,
// so this is directly testable.
import {createHash} from 'node:crypto';

/** Exactly narrate.py's text cleanup (core.VoxCPM._generate, normalize=False). */
export const cleanText = (t) => t.replace(/\n/g, ' ').replace(/\s+/g, ' ');

/** Re-derives a take key from the recorded ordered key fields plus the prompt. */
export const takeKey = (keyFields, prompt) =>
  createHash('sha256').update([...keyFields, cleanText(prompt)].join('|')).digest('hex').slice(0, 16);

export const KEY_FIELD_COUNT = 7;

/**
 * @param {{script:object, timings:object, anchor:object, ledger:object, anchorSha:string|null,
 *          registry?:object, wavHashes?:object}} input
 *   registry  — narration/take-hashes.json: the bytes each take is supposed to have
 *   wavHashes — the bytes actually staged for this render, keyed by take filename
 * @returns {string[]} problems; empty means the narration is provably the declared voice
 */
export function verifyProvenance({script, timings, anchor, ledger, anchorSha, registry, wavHashes}) {
  const v = script.voice;
  const problems = [];

  // 1. The anchor BYTES. Comparing recorded hashes to each other only proves the
  // metadata is self-consistent: replacing anchor.wav leaves it all agreeing while
  // every take was cloned from a different voice.
  if (!anchorSha) problems.push('narration/anchor.wav is missing — cannot verify the voice');
  else {
    if (anchorSha !== anchor.sha256)
      problems.push(`anchor.wav hashes to ${anchorSha.slice(0, 12)}… but anchor.json records ${String(anchor.sha256).slice(0, 12)}… — the voice on disk was replaced`);
    if (timings.anchorSha256 && anchorSha !== timings.anchorSha256)
      problems.push(`anchor.wav hashes to ${anchorSha.slice(0, 12)}… but the narration was measured against ${String(timings.anchorSha256).slice(0, 12)}…`);
  }

  // 2. The declared voice inputs.
  for (const [field, want] of [['id', v.id], ['voiceVersion', script.voiceVersion],
    ['description', v.description], ['anchorLine', v.anchorLine], ['seed', v.seed],
    ['anchorFrom', v.anchorFrom ?? '']])
    if (anchor[field] !== want)
      problems.push(`anchor.json ${field} is ${JSON.stringify(anchor[field])}, script says ${JSON.stringify(want)}`);
  if (timings.modelRevision && timings.modelRevision !== anchor.modelRevision)
    problems.push(`timings model ${timings.modelRevision} != anchor.json ${anchor.modelRevision}`);

  // 3. keyFields is what take keys are recomputed from, so it is checked against its
  // own declared inputs — including the freshly hashed anchor — before being trusted.
  const fields = timings.keyFields ?? anchor.keyFields;
  if (!fields) {
    problems.push('no keyFields recorded — re-run `npm run narrate`');
    return problems;
  }
  if (fields.length !== KEY_FIELD_COUNT) {
    problems.push(`keyFields has ${fields.length} components, expected ${KEY_FIELD_COUNT}`);
    return problems;
  }
  const expect = [['voice id', fields[0], v.id], ['voiceVersion', fields[1], script.voiceVersion],
    ['seed', fields[2], String(v.seed)], ['model revision', fields[5], anchor.modelRevision]];
  if (anchorSha) expect.push(['anchor sha', fields[6], anchorSha]);
  for (const [what, got, want] of expect)
    if (got !== want) problems.push(`keyFields ${what} is ${JSON.stringify(got)}, expected ${JSON.stringify(want)}`);
  // steps and cfg are numbers in timings.json but strings in the key.
  if (timings.steps != null && Number(fields[3]) !== Number(timings.steps))
    problems.push(`keyFields steps ${fields[3]} != recorded ${timings.steps}`);
  if (timings.cfgValue != null && Number(fields[4]) !== Number(timings.cfgValue))
    problems.push(`keyFields cfg ${fields[4]} != recorded ${timings.cfgValue}`);
  if (problems.length) return problems;

  // 4. Every take the script needs, keyed to this exact voice, AND the bytes that
  // answered it. The key proves what was asked for; the registry proves which audio
  // came back, so a replaced or adopted WAV can no longer slip through unnoticed.
  for (const [name, spec] of Object.entries(script.videos))
    for (const ch of spec.chapters)
      ch.lines.forEach((text, i) => {
        const file = `${ch.id}-${String(i).padStart(2, '0')}.wav`;
        const recorded = ledger[file];
        if (!recorded) return problems.push(`${name}/${file}: no ledger key — that take is unverified`);
        const want = takeKey(fields, `(${v.style})${text}`);
        if (recorded !== want)
          return problems.push(`${name}/${file}: ledger key ${recorded} != ${want} (voice or script changed)`);
        if (!registry) return;
        const known = registry[file];
        if (!known || !known.sha256) return problems.push(`${name}/${file}: no recorded sha256 — run scripts/register-takes.mjs`);
        const actual = wavHashes?.[file];
        if (!actual) return problems.push(`${name}/${file}: the staged WAV was not hashed`);
        if (actual !== known.sha256)
          problems.push(`${name}/${file}: staged audio ${actual.slice(0, 12)}… != registered ${known.sha256.slice(0, 12)}… — the recording was replaced`);
      });
  return problems;
}
