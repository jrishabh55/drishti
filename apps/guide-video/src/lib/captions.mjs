// Caption shaping: one spoken sentence becomes one or more on-screen phrases,
// each at most two lines. Timing is apportioned by character count inside the
// sentence's *measured* audio duration, so captions never outrun the voice.

export const MAX_PHRASE_CHARS = 84;
export const MAX_LINE_CHARS = 46;
/** A split that would leave a tail shorter than this is not worth making. */
export const MIN_PHRASE_CHARS = 25;
/** Cues closer together than this are joined, so the caption box does not blink. */
export const MAX_CAPTION_GAP = 0.3;

const BREAKS = [', ', '; ', ': ', ' and ', ' or ', ' but ', ' so ', ' because ', ' which ', ' that '];

/** Split one sentence into phrases of at most `maxChars`, preferring clause boundaries. */
export function splitIntoPhrases(text, maxChars = MAX_PHRASE_CHARS) {
  const rest = text.trim();
  if (rest.length <= maxChars) return [rest];
  // Best break is the latest clause boundary that still fits; fall back to the last space.
  let cut = -1;
  for (const b of BREAKS) {
    const i = rest.lastIndexOf(b, maxChars);
    if (i > maxChars * 0.35) cut = Math.max(cut, i + (b === ', ' || b === '; ' || b === ': ' ? b.length - 1 : 0));
  }
  if (cut < 0) cut = rest.lastIndexOf(' ', maxChars);
  if (cut < 0) cut = maxChars;
  const head = rest.slice(0, cut).trim();
  const tail = rest.slice(cut).trim();
  // Never strand a two-word fragment on screen for a second. If the remainder is too
  // short, back the cut off to an earlier break, or give up and keep the phrase whole.
  if (tail.length < MIN_PHRASE_CHARS) {
    const earlier = head.lastIndexOf(' ', head.length - MIN_PHRASE_CHARS);
    if (earlier > maxChars * 0.35) return [rest.slice(0, earlier).trim(), rest.slice(earlier).trim()];
    return [rest];
  }
  return [head, ...splitIntoPhrases(tail, maxChars)];
}

/** Wrap a phrase onto at most `maxLines` lines of at most `maxChars`, balancing length. */
export function wrapLines(phrase, maxChars = MAX_LINE_CHARS, maxLines = 2) {
  const words = phrase.split(/\s+/);
  if (phrase.length <= maxChars || maxLines === 1) return [phrase];
  // Balance: break as close to the middle as a word boundary allows.
  let best = null;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(' ');
    const b = words.slice(i).join(' ');
    if (a.length > maxChars || b.length > maxChars * maxLines) continue;
    const score = Math.abs(a.length - b.length);
    if (!best || score < best.score) best = {score, a, b};
  }
  if (!best) return [words.slice(0, 1).join(' '), words.slice(1).join(' ')];
  return [best.a, ...wrapLines(best.b, maxChars, maxLines - 1)];
}

/**
 * Cues for one spoken sentence.
 * @param {string} text @param {number} start seconds @param {number} duration measured seconds
 */
export function captionsForLine(text, start, duration) {
  const phrases = splitIntoPhrases(text);
  const total = phrases.reduce((n, p) => n + p.length, 0);
  let t = start;
  return phrases.map((phrase, i) => {
    const secs = i === phrases.length - 1 ? start + duration - t : (duration * phrase.length) / total;
    const cue = {start: round(t), end: round(t + secs), text: phrase, lines: wrapLines(phrase)};
    t += secs;
    return cue;
  });
}

const round = (n) => Math.round(n * 1000) / 1000;

/**
 * Close sub-second holes so the caption box stays on screen between sentences.
 * Only gaps up to `maxGap` are bridged; a real pause (a chapter change) still clears.
 */
export function bridgeGaps(cues, maxGap = MAX_CAPTION_GAP) {
  return cues.map((cue, i) => {
    const next = cues[i + 1];
    if (!next) return cue;
    const gap = next.start - cue.end;
    return gap > 0 && gap <= maxGap ? {...cue, end: round(next.start)} : cue;
  });
}

const stamp = (s) => {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${sec.toFixed(3).padStart(6, '0')}`;
};

/** WebVTT document for a list of cues. */
export function toVtt(cues) {
  return ['WEBVTT', '', ...cues.flatMap((c, i) => [String(i + 1), `${stamp(c.start)} --> ${stamp(c.end)}`, ...c.lines, ''])].join('\n');
}
