# Drishti Guide release — 2026-10-30

The contextual Guide and both narrated films are live at https://drishiti.codeation.io.
Open **Guide** → **This screen** for page help and video, **Tours** for the first
paper-trade path and 18-feature library, or **Ask** for sourced operational help.
Built-in help is free. Optional generated answers require an eligible personal
provider, an explicit selection and consent; no house keys or analysis credits
are used.

## Published media

Version: `guide-20261030-v1`. Catalog: `2026-10-30.2`.

- [Introduction (1:07)](https://drishiti.codeation.io/guide-media/guide-20261030-v1/intro.mp4)
- [Walkthrough (6:13)](https://drishiti.codeation.io/guide-media/guide-20261030-v1/walkthrough.mp4), 18 chapters
- [Manifest and complete SHA-256 list](https://drishiti.codeation.io/guide-media/manifest.json)

Both films: 1920×1080 H.264 at 30 fps, AAC stereo, original locally generated
English narration, burned-in captions and separate WebVTT captions. Illustrative
screens contain no real account data. Source and rendering scripts are in
`apps/guide-video`; model weights and rendered media stay outside Git and the
application images. Publication uses the existing persistent media volume and
hash-verified, atomic manifest-last publisher.

## Verification evidence

- Product Python suite: 2,446 passed, 103 subtests, 3 deselected; 42 existing warnings.
- Frontend: 74 tests passed; typecheck and production Node build passed.
- Media handler: 3 tests passed. Publisher: 5 tests passed.
- Video: 37 logic tests passed and TypeScript check passed.
- Catalog export is in sync. Core `tradingagents/` and `cli/` were unchanged.
- Both complete MP4s decoded without errors. Encoded true peaks measured
  −2.58 dBTP and −2.07 dBTP. Source-take hashes, all 71 speech transcripts and
  final full-film transcripts were checked; no material missing speech found.
- Inspected 22 chapter frames and encoded samples. Independent Claude Opus 5.5
  review finished with **0 Critical and 0 Important** findings after fixes.
- Downloaded all six public media files and recomputed their hashes against the
  reviewed manifest. Both MP4 HEAD requests returned 200; byte-range requests
  returned 206 and matched local bytes.
- Live Chrome playback verified for both films after restarting an unresponsive
  browser. Current-screen Limits chapter opened at 4:43; Time Machine chapter
  seek landed at 2:31 and playback advanced with readable captions. Fullscreen,
  pause and return to the Guide worked.
- Show page focused Resume guide. Enter restored the Guide at saved step 2/10;
  closing returned focus to the Guide launcher. No trade or settings were changed.
- All six workers in this release run were settled and released; no narration or
  Remotion processes remain. Production live ordering remains disabled.

## Limits

Audio review used transcripts, hashes, loudness measurements and browser playback;
no human listening assessment or speaker-similarity model was performed. Forty-four
voice takes predate the stronger anchor-binding cache format; that provenance
limitation is documented in the video README. Two optional review nits remain:
the pure provenance helper accepts an omitted registry (the render entry point
rejects it), and the poster is a slightly dim fade-in frame. Neither blocks this
release. Real personal-provider AI calls were deliberately not made during QA.
