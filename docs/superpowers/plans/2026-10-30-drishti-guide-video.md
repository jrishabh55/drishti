# Drishti Guide and Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved contextual guide, grounded questions and two narrated Remotion videos.

**Architecture:** A single JSON catalog drives screen help, tours, API grounding and video chapters. A root-mounted React provider owns the guide state; a product-only API resolves personal model credentials. Remotion/VoxCPM2 render offline, with versioned media served from a persistent Dokploy volume.

**Tech Stack:** React 19, TanStack Router/Start, shadcn, FastAPI, existing LangChain clients, Remotion, VoxCPM2, ffmpeg.

**Spec:** `docs/superpowers/specs/2026-10-30-drishti-guide-video-design.md` (approved by user's “go”).

## Global Constraints

- Keep core `tradingagents/` and `cli/` unchanged.
- Keep `KITE_ORDERS_ENABLED=0`.
- English voiceover with captions; original narrator, no Huntik recordings or changes.
- No server-key fallback, no analysis-credit debit, no autonomous product mutations.
- Preserve current Docker build contexts and the production Node target.
- No real trades, paid analysis, messages or personal-provider calls during QA.
- Execute through completion; the approved design explicitly avoids further intermediate approval gates.

## Review Focus

- Empty accounts/dynamic routes: explain prerequisites instead of inventing objects or hiding guidance.
- User changes and late API responses: never expose another user's progress or stale AI reply.
- Async anchors and mobile drawers: retain usable focus and reveal targets without unintended clicks.
- Provider failures/timeouts: never fall back to server keys, leak secrets or release concurrency early.
- Video delivery: byte ranges, traversal/symlinks, missing manifest and atomic upload are exercised.

## File ownership and interfaces

- Coordinator: `apps/web/src/guide/*`, root/Topbar/route anchors, `apps/web/guide-media.mjs`, server/vite wiring, compose/deploy integration, docs.
- Claude Opus 5.5 API implementer: only `apps/api/guide/*`, `apps/api/routes/guide.py`, route registration in `apps/api/app.py`, `tests/test_guide*.py`. No catalog source edits.
- Claude Opus 5 video implementer: only `apps/guide-video/*`, plus generated output in ignored `output/guide-video/`. No web/API/deploy edits.
- Fresh Claude Opus 5.5 reviewer: read-only entire change and video artifacts after implementation.

Catalog v1: `{version:string,features:Feature[]}`. Feature:
`{id,title,route,summary,body,keywords:string[],faqs:{question,answer}[],chapter:string,steps:{route,anchor,title,body,fallback}[]}`.
Routes use `$id` patterns. Step anchors are literal `data-guide` values; all steps explain/navigation only.
Canonical JSON: `apps/web/src/guide/catalog.json`; generated identical export: `apps/api/guide/catalog.json`.

API: `GET /api/guide/providers` returns `{providers:[{id,label,models:[{id,label}]}]}` for configured personal providers only.
`POST /api/guide/ask` accepts `{question,route,provider,model,consent:true}`; returns `{answer,sources:string[],mode:'ai'|'help',notice?:string}`.
Source strings are catalog IDs; frontend resolves links. Question limit 1000 chars, six excerpts, 30-second timeout, one in-flight request/user, ten requests per ten minutes/user, bounded bookkeeping.

Video manifest: `{version,intro:{src,poster,duration},walkthrough:{src,poster,duration},chapters:[{id,title,start}],files:[{name,sha256}]}`.
All src/poster paths are `/guide-media/<version>/<filename>`; manifest is `/guide-media/manifest.json`.
Render outputs: `intro.mp4`, `walkthrough.mp4`, respective `.vtt`, `poster.jpg`, `chapters.json`, `manifest.json`.

### Task 1: Catalog, help retrieval and resumable state

**Files:** `apps/web/src/guide/{catalog.json,content.ts,state.ts,guide.test.ts}`, `scripts/sync-guide-catalog.py`, generated API catalog.

**Interfaces:** `features`, `featureForRoute(path)`, `searchHelp(question,path)`, `readProgress(storage,userId)`, `writeProgress(storage,userId,progress)`; `Progress={catalogVersion,tourId,stepIndex,completedLessonIds}`.

- [ ] Write tests for specific dynamic-route matching, paper-credit FAQ retrieval, unrelated-question no-match, per-user storage/version reset/corruption.
- [ ] Run focused Vitest; observe missing-feature failures.
- [ ] Implement catalog covering every feature row, retrieval, route matching and safe state storage. Export deterministic API copy.
- [ ] Run focused tests plus export sync check; commit.

### Task 2: Grounded personal-key API (Claude Opus 5.5)

**Files:** as API ownership above.

**Interfaces:** consumes catalog and API contract above; uses existing auth middleware and credential/catalog validation.

- [ ] Write endpoint/service tests before implementation for personal credentials, excluded server providers, consent, prompt bounds, citations/fallback, concurrency/rate/timeout and error redaction.
- [ ] Observe failing focused pytest.
- [ ] Implement bounded tool-free responses, manual Gemini and existing EXTRA_PROVIDERS only. No model request in GET/providers; no secrets in responses/logs.
- [ ] Verify focused tests, report exact commands/results and changes. Coordinator integrates; no worker commits shared index.

### Task 3: Contextual widget and anchors

**Files:** `apps/web/src/guide/{GuideProvider.tsx,GuidePanel.tsx,GuideAsk.tsx,GuideVideo.tsx,guide.css,GuidePanel.test.tsx}`, root, Topbar and relevant routes/components.

**Interfaces:** `GuideProvider({children,userId})`, `useGuide().openGuide()`, same API/media contracts above.

- [ ] Write component tests for open/close, tabs, tour navigation without submitting/clicking, missing anchors, user transition and stale answers.
- [ ] Run tests and confirm missing widget fails.
- [ ] Implement one root provider inside activation gate, launch controls, route help, tours/checklist, read-only highlighting, typed help/AI consent/provider/model selection and chapter player. Use shadcn Dialog on mobile and existing tokens in both themes.
- [ ] Add literal anchors for catalog steps; validate routes and anchors against source. Never fabricate dynamic IDs.
- [ ] Run focused tests/typecheck and desktop/mobile manual checks; commit.

### Task 4: Remotion films and original narration (Claude Opus 5)

**Files:** `apps/guide-video/{package.json,src/*,scripts/*,README.md}`, ignored renders under `output/guide-video/`.

**Interfaces:** consumes catalog chapters, produces media manifest above. Python engine/model paths supplied via CLI/env.

- [ ] Create manifest/timeline checks for chapter IDs, caption intervals and measured audio duration before scene implementation.
- [ ] Build isolated Remotion package from official docs; use cohesive synthetic fixtures and real product claims. No live account captures.
- [ ] Generate and inspect original narrator audition using existing local VoxCPM2. Render all narration, then derive timing/captions from WAV durations.
- [ ] Render representative frames, then both full 1080p exports. Verify with ffprobe, scene timings and readable captions; report artifacts/remaining defects. No product deployment or worker commits.

### Task 5: Same-origin streaming and release integration

**Files:** `apps/web/{guide-media.mjs,guide-media.test.mjs,server-node.mjs,vite.config.ts}`, `docker-compose.dokploy.yml`, `deploy/publish-guide-media.py`.

**Interfaces:** `serveGuideMedia(request,root):Promise<Response|null>`; root via `GUIDE_MEDIA_DIR`. GET manifest and immutable versioned files, HEAD/range support.

- [ ] Write Node tests for MP4 MIME, full/suffix/invalid ranges, HEAD, missing files, traversal/symlinks, unrelated path fallback.
- [ ] Run `node --test apps/web/guide-media.test.mjs`; observe missing implementation failure.
- [ ] Implement streaming, guarded path handling, dev adapter and production early routing, read-only persistent mount and hash-verified manifest-last uploader with dry-run support.
- [ ] Run Node tests, compose validation and dry-run upload manifest checks; commit.

### Task 6: Whole product review, verification and deployment

**Files:** user guide/release notes plus any validated fixes.

- [ ] Fresh Opus 5.5 review of complete patch and source-based film claims. Resolve material findings with reproducing tests.
- [ ] Run `TZ=UTC .venv/bin/python -m pytest -q --tb=short`, `WEB_TARGET=node pnpm --dir apps/web test`, `pnpm --dir apps/web typecheck`, `WEB_TARGET=node pnpm --dir apps/web build`, Node media and video-manifest checks.
- [ ] Review representative frames, audio and full MP4 outputs; verify chapter playback and missing-media state.
- [ ] Integrate reviewed commits into main, publish media and deploy via existing Dokploy setup. Smoke-check guide, read-only help and media HEAD/range; no financial mutations.
- [ ] Close finished Claude sessions, record evidence and remaining limitations, deliver video links and how to open/use Guide.
