import {Audio} from '@remotion/media';
import React from 'react';
import {AbsoluteFill, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Captions} from './Captions';
import {ChapterScreen} from './scenes/fixtures';
import {IntroScene} from './scenes/intro';
import {NARRATION_GAIN} from './lib/mix.mjs';
import {TIMING} from './lib/timeline.mjs';
import {C} from './theme';


export type Timeline = {
  title: string;
  fps: number;
  durationInFrames: number;
  durationSeconds: number;
  audioSeconds: number;
  chapters: {
    id: string;
    title: string;
    start: number;
    end: number;
    startFrame: number;
    durationInFrames: number;
    lines: {file: string; text: string; seconds: number; start: number; startFrame: number; durationInFrames: number}[];
  }[];
  captions: {start: number; end: number; text: string; lines: string[]}[];
};

/** Contiguous frame ranges, so the gaps between chapters are never blank. */
const segments = (t: Timeline) => {
  const half = Math.round((TIMING.gapChapter * t.fps) / 2);
  return t.chapters.map((ch, i) => {
    const from = i === 0 ? 0 : ch.startFrame - half;
    const to = i === t.chapters.length - 1 ? t.durationInFrames : t.chapters[i + 1].startFrame - half;
    return {ch, from, durationInFrames: to - from};
  });
};

const FadeIn: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, 12], [0, 1], {extrapolateRight: 'clamp'});
  const y = interpolate(frame, [0, 16], [16, 0], {extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{opacity: o, transform: `translateY(${y}px)`}}>{children}</AbsoluteFill>;
};

const ChapterBadge: React.FC<{title: string; index: number; total: number}> = ({title, index, total}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, 10, 3.2 * fps, 3.8 * fps], [0, 1, 1, 0], {extrapolateRight: 'clamp'});
  return (
    <div
      style={{
        // Bottom-left, clear of the top bar and the desk tabs it used to cover,
        // and left of the centred caption block.
        position: 'absolute',
        left: 34,
        bottom: 20,
        maxWidth: 420,
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        opacity: o,
        background: 'rgba(10, 11, 14, 0.92)',
        border: `1px solid ${C.accentLine}`,
        borderRadius: 12,
        padding: '9px 18px',
        display: 'flex',
        alignItems: 'baseline',
        gap: 14,
      }}
    >
      <span style={{fontSize: 18, color: C.accentHi, fontWeight: 700, fontFamily: 'ui-monospace, Menlo, monospace'}}>
        {String(index + 1).padStart(2, '0')}/{total}
      </span>
      <span style={{fontSize: 25, color: C.text1, fontWeight: 600, letterSpacing: -0.3}}>{title}</span>
    </div>
  );
};

const Progress: React.FC = () => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 5, background: C.line1}}>
      <div style={{height: '100%', width: `${(frame / durationInFrames) * 100}%`, background: C.accent}} />
    </div>
  );
};

// defaultProps are serialized as JSON, so the scene is chosen by name, not by callback.
const SCENES = {
  intro: (id: string) => <IntroScene chapter={id} />,
  walkthrough: (id: string) => <ChapterScreen chapter={id} />,
};

export const Film: React.FC<{
  timeline: Timeline;
  kind: keyof typeof SCENES;
  showBadge?: boolean;
  showCaptions?: boolean;
  fontFamily: string;
}> = ({timeline, kind, showBadge = true, showCaptions = true, fontFamily}) => (
  <AbsoluteFill style={{background: C.bg0, fontFamily}}>
    {segments(timeline).map(({ch, from, durationInFrames}, i) => (
      <Sequence key={ch.id} from={from} durationInFrames={durationInFrames} name={ch.id}>
        <FadeIn>{SCENES[kind](ch.id)}</FadeIn>
        {showBadge ? <ChapterBadge title={ch.title} index={i} total={timeline.chapters.length} /> : null}
      </Sequence>
    ))}
    {timeline.chapters.flatMap((ch) =>
      ch.lines.map((l) => (
        <Audio key={l.file} src={staticFile(l.file)} from={l.startFrame} name={l.file} volume={NARRATION_GAIN} />
      )),
    )}
    {showCaptions ? <Captions cues={timeline.captions} /> : null}
    <Progress />
  </AbsoluteFill>
);
