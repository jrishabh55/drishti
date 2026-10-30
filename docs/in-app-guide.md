# Using the Drishti Guide

Open **Guide** in the top bar, the floating Guide button, or press **?** while
you are not typing. The guide is available after sign-in and activation.

**This screen** explains the page, common questions and prerequisites. Use
**Take this lesson** for its tour, or watch the corresponding video chapter.

The published films have English narration and captions: the
[introduction](https://drishiti.codeation.io/guide-media/guide-20261030-v1/intro.mp4)
(1:07) and the
[full walkthrough](https://drishiti.codeation.io/guide-media/guide-20261030-v1/walkthrough.mp4)
(6:13). The player includes 18 chapter links. Screens use an illustrative demo
account; they do not show the signed-in account or execute any action.

**Tours** contains the first paper-trade path and the feature library. Start
with paper capital and limits, research a company, add it to the watchlist,
make a plan, inspect Time Machine and risk checks, then deliberately confirm a
paper ticket. Telegram can carry paper proposals and updates after setup.
Review the ledger and Performance as eligible bars arrive. A tour does not
create or confirm a trade, run paid analysis, enter credentials or arm a broker.

Back, Next, Skip, Pause and Restart control the lesson only. Your place is
saved in this browser for your account. Reopening resumes an active tour.
**Show page** collapses the guide while keeping the target highlighted;
**Resume guide** restores the explanation. A missing target shows its
prerequisite and Retry/Skip controls. Completing a lesson is learning progress,
not proof that a product action succeeded.

**Ask** searches written help for free. Results show their source topic.
Optional AI answers require an eligible personal provider/model, a usage
notice and explicit opt-in. Analysis credits and server keys are not used.
The question and relevant written help go to that provider; your portfolio or
page contents are not sent automatically. Source links let you check answers.
Changing the question, screen, provider or account discards stale responses.

Gemini's personal-key setup is in the Analysis form. Other personal provider
keys are available under **Models**. Providers with only custom model IDs are
not offered for AI guide questions until a model allowlist exists. Written
help remains available without any key. Provider failures, usage limits or
unverifiable answers show a clear error or built-in fallback.

Paper capital is simulated trading money. Analysis credits are separate.
Missing/delayed bars, market closure and insufficient history can prevent a
setup, replay or simulated fill. Intraday Time Machine needs an entry time
and at least 20 complete prior session windows. Paper outcomes do not establish
live execution quality. Current production live ordering remains disabled.

## Video operations

The Remotion source and local narration scripts live in `apps/guide-video`.
VoxCPM2 and its model stay outside the repository and runtime images. Rendered
MP4s, VTT captions, poster and chapter metadata are verified against a manifest.

Upload that flat bundle to a staging directory on the deployment host, then
run `deploy/publish-guide-media.py --source <staging> --destination <volume>`.
Use `--dry-run` first. The publisher checks all hashes, rejects incompatible
or changed immutable versions, copies assets and atomically publishes the
manifest last. The web service mounts `guide_media` read-only at
`/app/guide-media`; `GUIDE_MEDIA_DIR` supplies the server path.

The dedicated `/guide-media/` handler streams assets and supports HEAD and
byte ranges for chapter seeking. It does not change API proxy or SSR routing.
An absent or invalid manifest gives the guide an honest unavailable state.
Development uses `GUIDE_MEDIA_DIR` or `output/guide-video/published`.

Catalog source is `apps/web/src/guide/catalog.json`. After edits, run
`python3 scripts/sync-guide-catalog.py`; CI/review checks use `--check`.
Increment its version when changing tours so incompatible saved progress is
reset with a notice.

Checks: product pytest, frontend Vitest/typecheck/Node build,
`node --test apps/web/guide-media.check.mjs`, catalog sync and video checks.
No test needs real trades, paid analysis, bot messages or personal-key calls.
