"""Generate the Drishti narration with a locally installed VoxCPM2.

The voice is *designed from a text description* (same approach as the local
VoxCPM2 install's design_voices.py): one anchor clip, then every sentence cloned
from that anchor so the timbre stays constant. No real person is cloned and
nothing outside this package is written.

  audition: .venv/bin/python scripts/narrate.py audition
  full:     .venv/bin/python scripts/narrate.py render

Engine + model are supplied by CLI/env:
  VOXCPM_PYTHON  interpreter that has voxcpm installed (used by run-narration.sh)
  VOXCPM_MODEL   path to a local VoxCPM2 checkout
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
import wave
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
OUT = Path(os.environ.get("GUIDE_VIDEO_OUT", REPO / "output/guide-video"))
MODEL = os.environ.get("VOXCPM_MODEL", "/Users/rishabhjain/Documents/ChatGPT/huntik/tools/voxcpm/models/VoxCPM2")
# The engine's documented default. Not lowered for speed: it measurably does not
# help here (the autoregressive LM loop dominates), and lowering it costs quality.
STEPS = int(os.environ.get("VOXCPM_TIMESTEPS", "10"))
# Off by default: an existing WAV with no recorded key is re-rendered, not trusted.
ADOPT_UNKNOWN = os.environ.get("VOXCPM_ADOPT_UNKNOWN") == "1"
CFG_VALUE = 2.0
# Package-local device choice. Unset = the engine's own default (MPS on this Mac).
# "cpu" is the supported lower-memory fallback: pick_runtime_dtype keeps the
# checkpoint's bfloat16 on CPU, so this is a device change only — no dtype override,
# no engine patch, no change to voice, seed or step count.
DEVICE = os.environ.get("VOXCPM_DEVICE") or None
OPTIMIZE = os.environ.get("VOXCPM_OPTIMIZE", "1") == "1"
SCRIPT = json.loads((PKG / "src/narration.json").read_text())

AUDITION = {
    "narrator-a": "A warm adult Indian English narrator, calm and measured, low mid-pitched, reassuring and precise",
    "narrator-b": "A clear adult Indian English narrator, bright and articulate, friendly and brisk, crisp diction",
    "narrator-c": "A warm and clear adult Indian English narrator, mid-pitched and calm, friendly and precise, speaking at an unhurried teaching pace",
}
AUDITION_LINES = [
    SCRIPT["voice"]["anchorLine"],
    "Paper capital is simulated trading money, and analysis credits are a separate balance.",
]


def load_model():
    from voxcpm import VoxCPM

    # load_denoiser=False: no ZipEnhancer download, and synthetic speech needs no denoising.
    kw = {}
    if DEVICE:
        kw["device"] = DEVICE
    if not OPTIMIZE:
        kw["optimize"] = False
    print(f"loading model on {DEVICE or 'engine default'} (optimize={OPTIMIZE})", flush=True)
    return VoxCPM.from_pretrained(MODEL, load_denoiser=False, **kw)


def clean(text: str) -> str:
    """Exactly core.VoxCPM._generate's text handling (normalize defaults to False)."""
    return re.sub(r"\s+", " ", text.replace("\n", " "))


def prompt_for(text: str) -> str:
    """The exact string handed to the engine: a delivery note plus the sentence."""
    return f"({SCRIPT['voice']['style']}){text}"


def sha256_file(path: Path) -> str:
    """Streaming hash — never loads a whole file into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def engine_version() -> str:
    try:
        from importlib.metadata import version

        return version("voxcpm")
    except Exception:
        return "unknown"


def weak_model_revision() -> str:
    """The revision formula this package shipped before path/mtime/engine were bound.
    Kept only so `migrate` can recognise takes recorded under it."""
    root = Path(MODEL)
    parts = [root.name]
    cfg = root / "config.json"
    if cfg.is_file():
        parts.append(sha256_file(cfg)[:16])
    for name in ("model.safetensors", "audiovae.pth"):
        f = root / name
        parts.append(f"{name}:{f.stat().st_size}" if f.is_file() else f"{name}:missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def model_revision() -> str:
    """A stable marker for the exact checkpoint in use, computed ONCE per run before
    the model is loaded. It deliberately does not hash the multi-GB weights; instead it
    binds the fully resolved path, the small config's content hash, and each weight
    file's size AND mtime_ns — a replacement checkpoint very often keeps the same name,
    size and config, so mtime and the absolute path are what actually separate them.
    The engine version is included because the same weights decode differently across
    engine releases."""
    root = Path(MODEL).resolve()
    parts = [str(root), f"engine:{engine_version()}"]
    cfg = root / "config.json"
    parts.append(f"config:{sha256_file(cfg)[:16]}" if cfg.is_file() else "config:missing")
    for name in ("model.safetensors", "audiovae.pth"):
        f = root / name
        if f.is_file():
            st = f.stat()
            parts.append(f"{name}:{st.st_size}:{st.st_mtime_ns}")
        else:
            parts.append(f"{name}:missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def anchor_identity() -> dict:
    """What the anchor is supposed to be. Changing any of this changes the voice."""
    v = SCRIPT["voice"]
    return {"id": v["id"], "voiceVersion": SCRIPT["voiceVersion"], "description": v["description"],
            "anchorLine": v["anchorLine"], "seed": v["seed"], "anchorFrom": v.get("anchorFrom", "")}


def legacy_take_keys(prompt: str, anchor_sha: str) -> list:
    """Every key format a valid take may have been recorded under. Used only by
    `migrate`, to prove a WAV was already correct before re-keying it. Each entry is
    a format this package actually shipped, so migration is auditable, not a guess."""
    v = SCRIPT["voice"]
    return [
        # v1: voice id + steps + prompt.
        hashlib.sha256(f"{v['id']}|{STEPS}|{clean(prompt)}".encode()).hexdigest()[:16],
        # v2: added anchor bytes, version, seed and cfg, with the model as a bare name.
        take_key(prompt, anchor_sha, Path(MODEL).name),
        # v3: a model revision from name + config + weight sizes, before path/mtime/engine.
        take_key(prompt, anchor_sha, weak_model_revision()),
    ]


def key_fields(anchor_sha: str, model_rev: str) -> list:
    """The exact STRING components of a take key, in order, minus the prompt. Written
    into anchor.json and timings.json so render.mjs can re-derive keys byte-for-byte
    without loading the engine."""
    v = SCRIPT["voice"]
    return [v["id"], SCRIPT["voiceVersion"], str(v["seed"]), str(STEPS), str(CFG_VALUE),
            model_rev, anchor_sha]


def take_key(prompt: str, anchor_sha: str, model_rev: str) -> str:
    """Identity of a rendered take, bound to everything that can change how it sounds:
    the anchor BYTES, the voice id and version, the seed, the step count, cfg and the
    model. Swapping the anchor therefore invalidates every take instead of silently
    mixing two voices into one film."""
    raw = "|".join([*key_fields(anchor_sha, model_rev), clean(prompt)])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def measure(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def legacy_generate(model, text: str, anchor: Path):
    """What core.generate() does: rebuilds and re-encodes the anchor cache every call."""
    return model.generate(text=text, reference_wav_path=str(anchor), cfg_value=2.0, inference_timesteps=STEPS)


def cached_generate(model, text: str, cache):
    """Same engine call, but against an anchor cache built once (voxcpm2.py:681/774)."""
    wav, _, _ = model.tts_model.generate_with_prompt_cache(
        target_text=clean(text),
        prompt_cache=cache,
        min_len=2,
        max_len=4096,
        inference_timesteps=STEPS,
        cfg_value=2.0,
        retry_badcase=True,
        retry_badcase_max_times=3,
        retry_badcase_ratio_threshold=6.0,
    )
    return wav.squeeze(0).cpu().numpy()


def gen(model, sf, torch, path: Path, text: str, seed: int, anchor, cache=None, ledger=None,
        anchor_sha="", model_rev="", generated=None):
    """Cached one-sentence-per-WAV generation. Returns measured duration in seconds."""
    key = take_key(text, anchor_sha, model_rev)
    # Fail closed. A WAV whose key is not recorded cannot be proven to match the current
    # script, so it is re-rendered rather than trusted. ADOPT_UNKNOWN=1 is the explicit,
    # reviewable escape hatch for a one-off ledger migration, and it says so in the log.
    recorded = None if ledger is None else ledger.get(path.name)
    if recorded is None and path.exists() and ADOPT_UNKNOWN:
        print(f"  ADOPTING {path.name} with no ledger entry (VOXCPM_ADOPT_UNKNOWN=1)", flush=True)
        stale = False
    else:
        stale = ledger is not None and recorded != key
    if not path.exists() or stale:
        if path.exists():
            print(f"  re-rendering {path.name}: script or voice changed", flush=True)
        torch.manual_seed(seed)  # installed voxcpm has no per-call seed
        t0 = time.monotonic()
        wav = cached_generate(model, text, cache) if cache is not None else legacy_generate(model, text, anchor)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, wav, model.tts_model.sample_rate)
        print(f"  gen {path.name} in {time.monotonic() - t0:.1f}s (steps={STEPS})", flush=True)
        if generated is not None:
            generated.add(path.name)
    if ledger is not None:
        ledger[path.name] = key
    return measure(path)


def audition():
    import soundfile as sf
    import torch

    model, seed = load_model(), SCRIPT["voice"]["seed"]
    report = {}
    for name, desc in AUDITION.items():
        d = OUT / "audition" / name
        anchor = d / "anchor.wav"
        gen(model, sf, torch, anchor, f"({desc}){SCRIPT['voice']['anchorLine']}", seed, None)
        takes = []
        for i, line in enumerate(AUDITION_LINES):
            p = d / f"line-{i}.wav"
            secs = gen(model, sf, torch, p, prompt_for(line), seed, anchor)
            takes.append({"file": str(p.relative_to(OUT)), "text": line, "seconds": round(secs, 3),
                          "wordsPerSecond": round(len(line.split()) / secs, 2)})
            print(name, i, takes[-1], flush=True)
        report[name] = {"description": desc, "anchorSeconds": round(measure(anchor), 3), "takes": takes}
    p = OUT / "audition/audition.json"
    p.write_text(json.dumps(report, indent=1))
    print("AUDITION", p)


def render():
    import soundfile as sf
    import torch

    # Computed BEFORE the model is loaded, once, and reused for every sentence key.
    model_rev = model_revision()
    model, seed = load_model(), SCRIPT["voice"]["seed"]
    desc = SCRIPT["voice"]["description"]
    root = OUT / "narration"
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "takes.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    # Separate registry: the key proves WHAT was asked for, this proves WHICH BYTES
    # answered. A replaced or adopted WAV changes its hash without changing its key.
    registry_path = root / "take-hashes.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}

    anchor = root / "anchor.wav"
    # Reuse the anchor the audition already produced for this voice; never re-generate it.
    prior = OUT / SCRIPT["voice"].get("anchorFrom", "")
    if not anchor.exists() and prior.is_file():
        shutil.copyfile(prior, anchor)
        print(f"reused audition anchor {prior.relative_to(OUT)}", flush=True)
    gen(model, sf, torch, anchor, f"({desc}){SCRIPT['voice']['anchorLine']}", seed, None)

    # Anchor provenance: the bytes on disk must be the ones this voice declares.
    anchor_sha = sha256_file(anchor)
    prov_path = root / "anchor.json"
    want = {**anchor_identity(), "sha256": anchor_sha, "modelRevision": model_rev,
            "engine": engine_version(), "device": DEVICE or "default",
            "keyFields": key_fields(anchor_sha, model_rev)}
    if prov_path.exists():
        have = json.loads(prov_path.read_text())
        drift = {k: (have.get(k), want[k]) for k in want if have.get(k) != want[k]}
        if drift and not ADOPT_UNKNOWN:
            raise SystemExit(
                f"anchor provenance mismatch: {drift}\n"
                "The voice on disk is not the voice narration.json declares. Delete "
                "narration/anchor.wav to re-render it, or set VOXCPM_ADOPT_UNKNOWN=1 to accept it.")
        if drift:
            print(f"  ACCEPTING anchor drift {drift} (VOXCPM_ADOPT_UNKNOWN=1)", flush=True)
    prov_path.write_text(json.dumps(want, indent=1))
    print(f"anchor {anchor_sha[:12]}… verified against {want['anchorFrom'] or 'generated'}; "
          f"model {model_rev}", flush=True)

    # Build the anchor cache once instead of re-encoding it on every sentence.
    t0 = time.monotonic()
    cache = model.tts_model.build_prompt_cache(reference_wav_path=str(anchor))
    print(f"built anchor prompt cache in {time.monotonic() - t0:.1f}s", flush=True)

    generated = set()  # take filenames this process actually rendered
    work = [(v, ch, i, text) for v, spec in SCRIPT["videos"].items()
            for ch in spec["chapters"] for i, text in enumerate(ch["lines"])]
    path_for = lambda v, ch, i: root / v / f"{ch['id']}-{i:02d}.wav"

    # Every sentence uses the cached anchor prompt. No runtime A/B: the one I ran was
    # confounded by process warmup, so it is not evidence either way, and re-encoding
    # the reference on every call is pure repeated work regardless.
    names = [path_for(v, ch, i).name for v, ch, i, _ in work]
    assert len(set(names)) == len(names), "take filenames must be unique across both films"
    # Drop ledger rows for lines the script no longer has. Their WAVs stay on disk.
    for gone in [n for n in ledger if n not in names]:
        print(f"  ledger: dropping {gone} — no longer in the script (WAV kept on disk)", flush=True)
        ledger.pop(gone)

    timings = {"voice": SCRIPT["voice"]["id"], "voiceVersion": SCRIPT["voiceVersion"], "steps": STEPS,
               "cfgValue": CFG_VALUE, "model": Path(MODEL).name, "modelRevision": model_rev,
               "engine": engine_version(), "device": DEVICE or "default", "anchorSha256": anchor_sha,
               "keyFields": key_fields(anchor_sha, model_rev),
               "promptCache": True, "sampleRate": model.tts_model.sample_rate, "videos": {}}
    for video, spec in SCRIPT["videos"].items():
        chapters = []
        for ch in spec["chapters"]:
            lines = []
            for i, text in enumerate(ch["lines"]):
                p = path_for(video, ch, i)
                secs = gen(model, sf, torch, p, prompt_for(text), seed, anchor, cache=cache,
                           ledger=ledger, anchor_sha=anchor_sha, model_rev=model_rev,
                           generated=generated)
                entry = registry.get(p.name) or {}
                # Only a take generated in THIS process gets this run's device recorded.
                device = DEVICE or "default" if p.name in generated else entry.get("device", "unknown")
                registry[p.name] = {"sha256": sha256_file(p), "device": device,
                                    "seconds": round(secs, 3)}
                lines.append({"file": str(p.relative_to(OUT)), "text": text, "seconds": round(secs, 3),
                              "device": device})
                ledger_path.write_text(json.dumps(ledger, indent=1))
                registry_path.write_text(json.dumps(registry, indent=1))
                print(video, ch["id"], i, round(secs, 2), flush=True)
            chapters.append({"id": ch["id"], "title": ch["title"], "lines": lines})
        timings["videos"][video] = {"title": spec["title"], "chapters": chapters}
    by_device = {}
    for e in registry.values():
        by_device[e["device"]] = by_device.get(e["device"], 0) + 1
    timings["device"] = "mixed: " + ", ".join(f"{k} {v}" for k, v in sorted(by_device.items())) \
        if len(by_device) > 1 else (next(iter(by_device), DEVICE or "default"))
    timings["devices"] = by_device
    p = root / "timings.json"
    p.write_text(json.dumps(timings, indent=1))
    registry_path.write_text(json.dumps(registry, indent=1))
    print("TIMINGS", p, "devices", by_device)


def migrate():
    """Explicit, verified re-key of takes recorded under the pre-provenance scheme.

    Every take is checked against the OLD key first: only a take that was already
    correct for this script and voice is carried over. Anything else is dropped from
    the ledger, so the next render re-voices just that line. No audio is deleted."""
    root = OUT / "narration"
    anchor = root / "anchor.wav"
    if not anchor.exists():
        raise SystemExit("no narration/anchor.wav — nothing to migrate")
    anchor_sha = sha256_file(anchor)
    model_rev = model_revision()
    declared = OUT / SCRIPT["voice"].get("anchorFrom", "")
    if declared.is_file() and sha256_file(declared) != anchor_sha:
        raise SystemExit(f"anchor.wav does not match the declared {SCRIPT['voice']['anchorFrom']}; refusing to migrate")
    print(f"anchor {anchor_sha[:12]}… matches {SCRIPT['voice'].get('anchorFrom') or '(generated)'}")

    ledger_path = root / "takes.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    # Separate registry: the key proves WHAT was asked for, this proves WHICH BYTES
    # answered. A replaced or adopted WAV changes its hash without changing its key.
    registry_path = root / "take-hashes.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    prompts = {f"{ch['id']}-{i:02d}.wav": prompt_for(t)
               for spec in SCRIPT["videos"].values() for ch in spec["chapters"] for i, t in enumerate(ch["lines"])}
    kept, dropped = {}, []
    for name, recorded in ledger.items():
        prompt = prompts.get(name)
        if prompt is not None and recorded in [*legacy_take_keys(prompt, anchor_sha),
                                               take_key(prompt, anchor_sha, model_rev)]:
            kept[name] = take_key(prompt, anchor_sha, model_rev)
        else:
            dropped.append(name)
    ledger_path.write_text(json.dumps(kept, indent=1))
    (root / "anchor.json").write_text(json.dumps(
        {**anchor_identity(), "sha256": anchor_sha, "modelRevision": model_rev,
         "engine": engine_version(), "keyFields": key_fields(anchor_sha, model_rev)}, indent=1))
    # The audio is proven unchanged, so timings.json's provenance block is brought onto
    # the current scheme too — otherwise the render verifier would see the ledger and the
    # measurements disagreeing about a metadata field and refuse a correct narration.
    t_path = root / "timings.json"
    if t_path.exists():
        t = json.loads(t_path.read_text())
        if t.get("anchorSha256") not in (None, anchor_sha):
            raise SystemExit(f"timings.json was measured against anchor {t['anchorSha256']}, not {anchor_sha}")
        t.update({"anchorSha256": anchor_sha, "modelRevision": model_rev, "engine": engine_version(),
                  "cfgValue": CFG_VALUE, "steps": STEPS, "keyFields": key_fields(anchor_sha, model_rev)})
        t_path.write_text(json.dumps(t, indent=1))
        print(f"timings.json provenance updated to model {model_rev}, engine {engine_version()}")
    print(f"re-keyed {len(kept)} takes; dropped {len(dropped)} for re-render: {dropped or 'none'}")


if __name__ == "__main__":
    {"audition": audition, "render": render, "migrate": migrate}[sys.argv[1] if len(sys.argv) > 1 else "render"]()
