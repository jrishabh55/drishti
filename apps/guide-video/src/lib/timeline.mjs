// Builds a frame-accurate timeline from the *measured* narration WAV durations
// in output/guide-video/narration/timings.json. Nothing here guesses a length.

import {bridgeGaps, captionsForLine} from './captions.mjs';

export const FPS = 30;
export const TIMING = {leadIn: 0.6, gapLine: 0.25, gapChapter: 0.7, tailOut: 1.2};

const frames = (seconds) => Math.round(seconds * FPS);

/**
 * @param {{title:string,chapters:{id:string,title:string,lines:{file:string,text:string,seconds:number}[]}[]}} video
 * @param {typeof TIMING} timing
 */
export function buildTimeline(video, timing = TIMING) {
  let t = timing.leadIn;
  const captions = [];
  const chapters = video.chapters.map((ch, ci) => {
    if (ci > 0) t += timing.gapChapter;
    const start = t;
    const lines = ch.lines.map((line, li) => {
      if (li > 0) t += timing.gapLine;
      const entry = {...line, start: t, end: t + line.seconds, startFrame: frames(t), durationInFrames: frames(line.seconds)};
      captions.push(...captionsForLine(line.text, t, line.seconds));
      t += line.seconds;
      return entry;
    });
    return {id: ch.id, title: ch.title, start, end: t, startFrame: frames(start), durationInFrames: frames(t - start), lines};
  });
  const durationSeconds = t + timing.tailOut;
  return {
    title: video.title,
    fps: FPS,
    durationSeconds,
    durationInFrames: Math.ceil(durationSeconds * FPS),
    audioSeconds: video.chapters.reduce((n, c) => n + c.lines.reduce((m, l) => m + l.seconds, 0), 0),
    chapters,
    // Bridged once here, so the burned-in captions and the VTT are identical.
    captions: bridgeGaps(captions),
  };
}

/** Chapter markers for the manifest / chapters.json. */
export const chapterMarkers = (timeline) =>
  timeline.chapters.map((c) => ({id: c.id, title: c.title, start: Math.round(c.start * 1000) / 1000}));
