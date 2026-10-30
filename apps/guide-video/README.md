# apps/guide-video

Offline Remotion package that renders the two Drishti guide films:

| Composition  | File              | Content |
|--------------|-------------------|---------|
| `Intro`      | `intro.mp4`       | What the desk is, the working loop, what stays guarded, where the Guide lives |
| `Walkthrough`| `walkthrough.mp4` | One chapter per catalog chapter id, in catalog order |

Lengths are whatever the measured narration makes them — see `manifest.json`, whose
durations are read back out of the encoded files with `ffprobe`. No title or metadata
states a duration, so a re-take can never put the file at odds with its own label.

Both are 1920×1080 H.264, English narration, burned-in captions plus a sidecar `.vtt`.

Nothing in this package touches `apps/web`, `apps/api`, `deploy/` or the agent core. All
rendered media lands in the ignored `output/guide-video/` tree, never in Git.

## Where the content comes from

Every claim on screen and in the script is grounded in `apps/web/src/guide/catalog.json`
(coordinator-owned, read-only here). `src/lib/timeline.test.mjs` fails if the walkthrough
chapters stop matching the catalog's chapter ids or their order.

The screens are **synthetic fixtures** for one fictional demo desk (`src/scenes/AppShell.tsx`,
`DEMO`). There are no captures of a real account, no real API keys or identities, no profit
claims, and no automation the product does not have:

* Time Machine shows the honest "0 of 20 required session windows — no replay was run" state.
  No replay result is invented.
* Live order execution is shown as **disabled**; only guarded, illustrative controls appear.
* Telegram is the featured message channel. WhatsApp appears as optional and not set up.
* The paper ledger and performance screens show a losing six-trade sample, charges included.

## Narration

The narrator is an **original designed voice** — a text description is turned into one anchor
clip, and every sentence is then generated against that anchor so the timbre stays constant.
No real person is cloned, no character voice is reused, and no file outside this repo is
modified. The engine is an existing local VoxCPM2 install; its weights and virtualenv are
never copied into Git.

```sh
# engine + model live outside the repo; override for another machine
export VOXCPM_PYTHON=/path/to/voxcpm/.venv/bin/python
export VOXCPM_MODEL=/path/to/models/VoxCPM2

npm run narrate:audition   # three candidate narrators -> output/guide-video/audition/
npm run narrate            # anchor + every sentence  -> output/guide-video/narration/
node scripts/audio-check.mjs audition
node scripts/audio-check.mjs narration
```

Generation is cached one sentence per WAV, with a fixed `torch.manual_seed` and
`load_denoiser=False`. The anchor's prompt cache is built **once** per process
(`build_prompt_cache`), then every sentence goes through `generate_with_prompt_cache`,
so the reference is not re-encoded 71 times.

Two artefacts guard the narration, and they answer different questions:

* `narration/takes.json` — the take **key**: what was asked for.
  `sha256(voice id | voiceVersion | seed | steps | cfg | model revision | anchor sha256 |
  style-prefixed sentence)`. The model revision binds the resolved checkpoint path, its
  `config.json` hash, each weight file's size and `mtime_ns`, and the engine version, so a
  replacement checkpoint with the same name is a different revision. The anchor sha binds
  the voice itself: swap the anchor and every key changes.
* `narration/take-hashes.json` — the take **registry**: which bytes answered, plus the
  device that produced each one. Added separately so it cannot invalidate an existing key.
  `node scripts/register-takes.mjs` backfills it for recordings made before it existed.

Re-running reuses a WAV only when its recorded key still matches. Edit a sentence and only
that sentence re-renders. This **fails closed**: a WAV whose key is not recorded is
re-rendered rather than trusted. `VOXCPM_ADOPT_UNKNOWN=1` is the explicit escape hatch for
a one-off migration, and every adoption it allows is printed.

`./scripts/run-narration.sh migrate` re-keys existing takes after a key-scheme change. It
verifies the anchor against its declared source, accepts only takes proven correct under a
key format this package actually shipped, and regenerates nothing.

`VOXCPM_DEVICE=cpu VOXCPM_OPTIMIZE=0` runs the engine on CPU — roughly half the weight
memory, and the supported fallback when MPS is thrashing. It changes the device only: the
engine keeps the checkpoint dtype, and the voice, seed and step count are untouched. `narration/timings.json` records the **measured** duration of every
take — the video timeline, the caption intervals and the chapter starts are all derived
from those measurements, never from an estimate.

`scripts/audio-check.mjs` is a measured QC pass (peak/mean dBFS, speech rate, trailing
silence) that catches clipped, silent, truncated or runaway takes.

## Render

```sh
npm install
npm run check              # timeline + caption + manifest contract checks
npm run narrate            # must run first: the timeline needs real durations
npm run frames             # one still per chapter -> output/guide-video/frames/
npm run render             # captions, chapters, both MP4s and the poster
npm run manifest           # hashes the finished files and writes manifest.json last
```

`--public-dir` points at `output/guide-video/render-public/`, a staging directory holding
hard links to the narration **only**. Pointing it at the whole output tree let every bundle
copy the auditions, the frames and previously rendered films.

`render.mjs all` refuses to overwrite a version directory that already holds a
`manifest.json`, because `guide-media` serves those files as immutable for a year. Bump
`GUIDE_MEDIA_VERSION`, or set `GUIDE_MEDIA_FORCE=1` if the version was never published.

Before a frame is drawn, `render.mjs` refuses to render:

* audio that no longer matches the script — chapter ids, counts and every line's text in
  `timings.json` must equal `narration.json`;
* audio that is not provably the declared voice — `narration/anchor.wav` is hashed and
  compared with `anchor.json` and `timings.json`, `keyFields` is validated against its own
  declared inputs, every per-take key is recomputed, and every staged WAV is hashed against
  the registry.

`npm run check` works on a fresh clone: `check:types` writes a stub
`src/timings.generated.json` when the real one is missing, so `tsc` and the Studio do not
need the narration engine to have run.

## Outputs

`output/guide-video/guide-20261030-v1/`

```
intro.mp4  walkthrough.mp4      1920x1080 H.264 + AAC
intro.vtt  walkthrough.vtt      WebVTT, same cues as the burned-in captions
poster.jpg                      shared poster still
chapters.json                   chapter markers for both films
manifest.json                   written last, see below
```

```jsonc
{
  "version": "guide-20261030-v1",
  "intro":       {"src": "/guide-media/<version>/intro.mp4", "poster": "…/poster.jpg",
                  "captions": "…/intro.vtt", "duration": 0},
  "walkthrough": { … },
  "chapters": [{"id": "orientation", "title": "Start here", "start": 0.6}],
  "files": [{"name": "intro.mp4", "sha256": "…"}]
}
```

`src/lib/manifest.mjs` owns that contract: the version must look like `guide-YYYYMMDD-vN`,
durations are read back out of the encoded files with `ffprobe`, every rendered file must be
hashed, and `manifest.json` is refused if it tries to hash itself.

## What this package does not establish

* **Voice consistency was not judged by ear.** No human listened to the 71 takes and no
  speaker-embedding similarity was computed. What is established is: every line was
  transcribed and matches its script text; the anchor on disk equals the declared
  `audition/narrator-a/anchor.wav`; and every take's bytes match its registry entry.
* **44 takes were generated on MPS and 27 on CPU** after a mid-run device fallback. Device
  is a numerics difference, not a voice change — the anchor, seed, cfg and step count are
  identical — but it was not verified acoustically. `timings.json` records the mix and each
  take's device.
* 44 takes were migrated across key-scheme changes. Each was carried over only after its
  recorded key matched a format this package shipped, but the earliest format did not bind
  the anchor, so their anchor provenance rests on the checks above rather than on that key.
* Fonts are vendored (`src/fonts/`, SIL OFL 1.1 — see `OFL.txt`), so a render needs no
  network and does not depend on a CDN.

## Captions

One spoken sentence becomes one or more on-screen phrases (`src/lib/captions.mjs`): at most
84 characters per phrase, at most **two** lines of at most 46 characters, split at clause
boundaries. Time inside a sentence is apportioned by character count within that sentence's
measured duration, so a caption can never outrun the voice.

## Layout

```
src/index.ts              registerRoot
src/Root.tsx              the two <Composition>s, sized from the measured timeline
src/Film.tsx              chapter sequencing, narration tracks, chapter badge, progress
src/Captions.tsx          burned-in captions
src/scenes/AppShell.tsx   synthetic product shell + the demo desk fixture data
src/scenes/fixtures.tsx   one screen per catalog chapter
src/scenes/intro.tsx      the four intro beats
src/lib/*.mjs             pure logic (timeline, captions, manifest) + `node --test` checks
scripts/narrate.py        VoxCPM2 voice design and narration
scripts/render.mjs        sidecars, stills and the two renders
scripts/manifest.mjs      hashing and the manifest, written last
scripts/audio-check.mjs   measured narration QC
```
