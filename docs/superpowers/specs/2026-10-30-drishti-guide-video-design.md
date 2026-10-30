# Drishti contextual guide and introduction video

Status: approved by the user (go); layout A selected, implementation in progress.

## Intent and agreed choices

Drishti needs to teach a new user how to operate the product, especially how
paper capital becomes a planned, confirmed, simulated trade and how to review
the result. The user wants both an in-app walkthrough bot and a Remotion
launch/introduction video that also teaches the workflows.

The user selected **A: a contextual guide beside the current screen**, with
**guided tours and questions**, and **English voiceover with captions** for
the video. They requested Claude Opus 5 and 5.5 participation and asked us to
reuse the voice-generation engine from their Huntik project.

Opus 5 produced an initial storyboard; Opus 5.5 reviewed guide architecture.
Those bounded design sessions finished and were closed. Their proposals are
inputs, not a substitute for checking claims against the application code.

## Goals, boundaries and completion

- Explain every user-facing feature through a searchable help library,
  relevant screen help, and a matching tour or explicitly explanatory lesson.
- Make the first paper-trade path understandable on a new, empty account.
- Provide resumable tours and questions from any activated application screen.
- Deliver a short introduction and a longer chaptered walkthrough from shared
  Remotion scenes, with original English narration and readable captions.
- Teach app operation, prerequisites and data limitations. Do not recommend
  investments, predict profits or present simulation as live execution evidence.
- Keep core `tradingagents/` and `cli/` unchanged. Product work belongs in
  `apps/web`, `apps/api`, new video/shared-content packages, tests and docs.

Completion means the widget and all catalog entries work, free-form questions
have a working grounded answer path and an honest unavailable state, and both
videos are rendered and visually/audio reviewed. A design preview, storyboard,
unrendered Remotion project or static FAQ alone does not complete the request.

## Approaches considered

**A — contextual guide, chosen by user.** A compact dock opens beside the task
on desktop and becomes a drawer on mobile. A library inside it gives access to
all features. This preserves context but requires careful layout and focus.

**B — dedicated learning page.** A larger library provides lesson/video space,
with small step cards after returning to the app. It gives room for reading
but adds navigation between learning and operating. The user chose A instead.

## Guide interface

Add a labeled Guide button in the shared Topbar and a small unobtrusive launcher
when the dock is closed. Mount its provider once behind the existing Clerk
sign-in and activation gates in the root shell; do not remount it per page.

Three tabs:

1. **This screen:** purpose, prerequisite/data-state help, common questions,
   relevant tours and the matching video chapter.
2. **Tours:** a recommended getting-started checklist plus all feature lessons.
   Each tour supports Back, Next, Skip step, Pause/close, Resume and Restart.
3. **Ask:** typed questions and suggested questions, with visible source links
   and clear distinction between built-in help and optional AI assistance.

Use existing theme tokens and shadcn primitives. The actual product keeps its
current layout; the mockup's surrounding desk is illustrative. On desktop the
dock should reserve space where available; when an overlay is necessary it
must not cover the highlighted target. On mobile it is a dismissible bottom
drawer that can collapse to reveal the target. Esc closes, focus returns to
the launcher, reduced motion is respected, and status text is accessible.

Tours may highlight and scroll visible controls and navigate to ordinary
product routes. They must never call an element's click handler, submit a
form, populate credentials, or invoke any mutation/paid analysis endpoint.
Every product action remains the user's deliberate action. Next advances the
lesson and never means Confirm trade. Completing a lesson is not proof that a
trade, broker connection or analysis succeeded.

## Content, routing and tour state

Create one versioned, serializable feature catalog at
`apps/web/src/guide/catalog.json`, with stable feature/lesson IDs, route
patterns, titles, prose, FAQs, source references, optional video chapter IDs
and tour steps. Web and video import it through typed adapters. Generate a
committed JSON export under `apps/api/guide/` for Python; a deterministic sync
check catches drift. This keeps web Docker builds self-contained in their
existing `apps/web` context and API builds in their existing repository-root
context. No manual independent copies of help text in three places.

Each step includes route, stable `data-guide` anchor, instruction and optional
prerequisite/fallback. Dynamic routes are resolved only from the current page
or an explicit user-selected object; never invent a plan/run ID. If there is
no object, show the list/create-screen lesson and explain what the user must
do to proceed. Query strings, symbols and private object IDs do not become
help-document source IDs.

Use bounded, read-only anchor observation for late-loading controls. An anchor
must exist, be visible and have a nonzero rectangle. Missing targets get an
explanation with Retry/Skip or a navigation option. No pointer to a guessed
coordinate. Clear observers and highlights on close/route change.

Persist only `{catalogVersion, tourId, stepIndex, completedLessonIds}` in
localStorage, namespaced by Clerk user ID. No chat, credentials or account data
is persisted. Storage failures fall back to memory. Version mismatches reset
the incompatible tour with a notice. Sign-out clears in-memory state; another
user never inherits the previous user's progress. Cross-device sync and
onboarding analytics are outside this release.

## Feature coverage and learning order

| Area | What the guide teaches |
| --- | --- |
| Orientation | Paper capital vs analysis credits; navigation; delayed data |
| Models | Personal provider keys, model selection, credits, helper connection |
| Analysis | Symbol/date, analysts/depth, deliberate Run, progress, reports/export |
| Scanners | Create/edit a scanner, understand a result and its data timestamps |
| Research | News/deals/results panels and evidence limitations |
| Today/watchlist | Company/symbol search, watchlist, opening range, entry timer |
| Plans | Entry, stop, target, exit time, draft editing and prerequisites |
| Time Machine | Intraday vs swing, prior-history requirements, simulations/history |
| Sizing/risk | Limits, sizing, failed checks, ticket review |
| Telegram | Configure/link/verify, alerts, paper approval/rejection/status/expiry |
| Paper ledger | Eligible entry, simulated fills/exits, cash, charges and P&L |
| Performance | Review actual paper outcomes and expected vs observed results |
| Alerts/limits | Alert rules, refresh, loss/order/position constraints |
| Backtests | Evaluation setup, deliberate execution, inspecting results |
| Zerodha/live | Connection, live-data distinction, daily arming, per-order confirmation, kill switch |

First-use recommendation: orientation → limits → research/watchlist → plan →
Time Machine → size/risk/ticket → deliberate paper confirmation (or Telegram)
→ ledger/performance. Models/analysis and scanners are optional branches, not
mandatory spending before a first paper trade. Advanced live features are
explained as guarded workflows; the guide does not enable or exercise them.

Market-closed, missing-quote, insufficient-history, unconfigured-provider and
empty-account states are part of the lessons. In particular, Time Machine
requires prior historical session windows; it does not simply replay today's
bars. Paper trade updates depend on eligible data and rules; no invented fill
or promise of immediate execution appears in the guide or video.

## Questions and cost policy — builder proposal for approval

Built-in help/search works without a model. It searches the catalog locally,
returns relevant source excerpts and links, and says when nothing matches.
It must not pretend keyword retrieval is a generated AI response.

Optional **AI answers** use a new authenticated `POST /api/guide/ask` route.
The user explicitly selects an available personal provider/model and enables
AI assistance after a notice that their provider may charge for questions.
The initial eligible set is saved manual Gemini keys and supported personal-key
providers in `product_providers.EXTRA_PROVIDERS`, using existing fixed provider
endpoints and model validation. OpenAI server credits, administrator providers,
OAuth and the ChatGPT helper are excluded from guide AI in this release: their
existing analysis accounting/lifecycle must not be silently reused for chat.
No new personal-key storage flow is required.

There is no default provider: the user explicitly selects an eligible
configured provider/model in the guide. Gemini is supported, not privileged.

The backend verifies activation and ownership exactly as other product APIs,
resolves credentials server-side, and never falls back to a house/server key.
No key means built-in help remains available with a Model connections link.
Do not auto-run the multi-agent analysis graph for a help question.

Each AI call receives the normalized route pattern, at most six relevant
catalog excerpts and the current question. No DOM dump, portfolio, plan data,
credentials, user-generated notes or chat history is sent automatically.
Show this boundary next to AI opt-in; questions are sent to the selected
provider, so the user should not include secrets. Keep conversation in memory
only and do not log request/response text.

Use server-enforced question/context/output limits, a 30-second timeout, one
in-flight call per user and a bounded per-user rate limiter. The concrete
limits are implementation defaults, adjustable without changing the billing
policy. Return answer text plus validated catalog source IDs. Invalid/missing
citations produce a sourced built-in fallback or an explicit inability to
answer. Citations alone cannot prove accuracy, so prompts and evaluation
cases must also check answer faithfulness and out-of-scope abstention.

The assistant has no tools and cannot trade, run analysis, change settings or
link accounts. Requests for stock picks or personalized investment decisions
are redirected to operational help. Render text/Markdown safely; source links
are resolved from known catalog IDs rather than model-provided URLs.

## Video and voice — builder proposals for approval

Produce two 1920×1080 exports initially: a roughly **75-second introduction**
and a **5–7 minute chaptered walkthrough**. Durations follow measured narration
after script review, rather than forcing speech into guessed timings. A
vertical cut can be added later; it is not required for this first release.

Create a separate `apps/guide-video/` Remotion package. Reuse small source-based
UI scenes, one coherent synthetic account and shared feature/chapter IDs.
Do not capture the production user's account, tokens, broker or Telegram
identity. Any simplified app views are labeled illustrative when necessary.
No synthetic profit history, unsupported features or misleading execution
claims. Final copy must be checked against product code, especially Time
Machine and paper/live differences.

Intro arc: meet Drishti → analysis/research → plan and risk → Telegram paper
approval → paper practice/review → built-in guide. The longer export covers
every row of the feature map, including model setup, backtests, missing-data
states and guarded live controls. The guide references matching video chapters.

Reuse **VoxCPM2 2.0.3** from
`/Users/rishabhjain/Documents/ChatGPT/huntik/tools/voxcpm` as a local generation
engine. Installed environment, model presence and Apple MPS availability were
checked. Create an original warm, clear Indian-English narrator, with an
anchor and consistent delivery. Do not modify Huntik or reuse its character
voice recordings. Configure engine/model paths; never commit a virtualenv or
model weights. Generate a short audition before the full narration.

Render narration first, measure every WAV, then derive scene durations and
caption intervals. Deliver MP4s, burned-in captions, separate subtitle files,
chapter metadata and a poster. Large generated media remains outside Git;
source/scripts and a reproducible asset manifest are committed. Publish
reviewed media to a dedicated persistent Dokploy volume mounted read-only by
the web service, with versioned/hash-qualified filenames. The release script
uploads assets, verifies hashes and publishes the matching manifest last.
Keep TTS/Remotion and model weights out of runtime images. No object storage
account or external publishing service is required.

Add a narrowly scoped same-origin `/guide-media/` file-serving path to the
Node server, rooted in that mounted directory. It must stream MP4s, support
HEAD and byte ranges for chapter seeking, provide MP4/VTT MIME types and
appropriate caching, and reject traversal. The current static server reads
entire files and has no MP4 MIME/range support, so merely copying videos into
`dist/client` is insufficient. Keep the existing API proxy, SSR and unrelated
static paths intact. The guide shows a clear unavailable state if videos have
not been published; a missing video is still unfinished work for this request.
Development uses the same manifest and local media directory via a dev-only
serving adapter. The media is intentionally public introductory content and
contains no account data.

Claude Opus 5 must participate in implementing the Remotion scenes/scripts,
and Opus 5.5 must review the guide integration, correctness and video claims,
as the user requested both. Use bounded tasks with disjoint writable scopes
and close each session on finish.

The builder and independent reviewer check the audition, script, sample
frames and full rendered videos without stopping for user approval at each
stage. Deliver the final videos for user feedback. The approved design is
the implementation checkpoint; another decision is needed only if a material
constraint changes. Do not publish videos to external platforms or audiences
as part of creating them. The same-origin product release includes reviewed
media as described above.

## Verification and release

- Catalog coverage, route patterns, source IDs and exported JSON stay in sync.
- Tour-state tests cover resume/version changes/user isolation/storage failure;
  component tests cover navigation, missing anchors and no mutation execution.
- Question API tests use stubbed providers: authentication, activation, user
  credential isolation, no server fallback, limits/timeouts, citations and
  abstention. No paid model requests are required for automated tests.
- Faithfulness checks include source-grounded prompt/answer fixtures and
  independent review; do not claim these establish a real model's accuracy.
  Live model benchmarking is outside the release gate. The owner can exercise
  real AI answers after selecting their personal provider and opting in from
  the widget. Agents do not spend personal-key tokens silently during QA.
- Check desktop/mobile layout, keyboard/focus, both themes and reduced motion.
- Render representative video frames first; review the full outputs, audio,
  caption legibility/synchronization and chapter seeking before packaging.
- Run product Python/frontend checks and production Node build. Existing
  broker, messaging, analysis and paper behavior must remain intact.
- Keep `KITE_ORDERS_ENABLED=0`. Testing tours and video requires no real trades,
  paid analysis runs, messaging recipients or broker arming.
- Make the implementation reviewable before deployment. Deploy only the
  reviewed product/video bundle, then smoke-check guide/help/media availability
  through Dokploy without exercising financial actions.

## Builder-owned decisions

The builder may choose component filenames, the minimal tour state-machine
implementation, exact anchor placement, retrieval scoring, supported model
selection UI, numerical request limits, narrator take, scene timing and media
compression. These choices must preserve the interface, cost boundary,
feature coverage and safety constraints above. No separate learning page,
house-funded chat or autonomous actions may be added without changing this
design explicitly.
