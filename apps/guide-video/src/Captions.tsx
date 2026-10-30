import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {C} from './theme';

type Cue = {start: number; end: number; text: string; lines: string[]};

/** Burned-in captions: at most two lines, timed from the measured narration. */
export const Captions: React.FC<{cues: Cue[]}> = ({cues}) => {
  const {fps, width} = useVideoConfig();
  const t = useCurrentFrame() / fps;
  const cue = cues.find((c) => t >= c.start && t < c.end);
  if (!cue) return null;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 92, display: 'flex', justifyContent: 'center'}}>
      <div
        style={{
          maxWidth: width * 0.6,
          background: 'rgba(6, 8, 12, 0.88)',
          border: `1px solid ${C.line2}`,
          borderRadius: 12,
          padding: '16px 30px',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        {cue.lines.map((l, i) => (
          <div key={i} style={{fontSize: 38, lineHeight: 1.28, color: C.text1, textAlign: 'center', fontWeight: 500, letterSpacing: -0.2}}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
};
