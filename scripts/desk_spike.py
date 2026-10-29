#!/usr/bin/env python3
"""Trade Desk data spike (spec §3.4 / Phase 0). Run it FROM THE DEPLOYED
SERVER first, then from a home machine, and compare:

    python scripts/desk_spike.py                  # every dataset, 3 rounds, 10 min apart
    python scripts/desk_spike.py --rounds 1       # quick check
    python scripts/desk_spike.py --only nse.fo_ban,nse.price_bands
    python scripts/desk_spike.py --yahoo RELIANCE,SBIN,TATAMOTORS   # + 5m bar checks

For each dataset it prints the HTTP status, size and content type, and saves
the body to tests/fixtures/desk/spike/<host-label>/<dataset>.<ext> so parsers
can be tested against REAL responses. A dataset that returns 200 in every
round from the server stays route="server" in apps/api/desk/datasets.py;
one that's blocked moves to route="helper".

The Yahoo check fetches 59 days of 5m bars per symbol and reports: whether
bars start at 09:15 IST, how many full 75-bar sessions there are, and the
delay of the newest bar vs now (run it during market hours for the delay).
Standard library + requests + yfinance (already project dependencies).
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.desk import datasets  # noqa: E402

SAMPLE_PARAMS = {
    "nse.announcements": {"symbol": "RELIANCE"},
    "nse.financial_results": {"symbol": "RELIANCE"},
    "nse.insider_pit": {"from": (datetime.now() - timedelta(days=7)).date().isoformat(),
                        "to": datetime.now().date().isoformat()},
}


def probe(name: str, out_dir: Path) -> dict:
    ds = datasets.CATALOG[name]
    url = datasets.build_url(ds, SAMPLE_PARAMS.get(name, {}))
    t0 = time.time()
    try:
        body = datasets._http_get(ds, url)
        status, err = 200, None
    except datasets.FeedUnavailable as exc:
        body, status, err = b"", None, str(exc)
    except Exception as exc:  # noqa: BLE001
        body, status, err = b"", None, f"{type(exc).__name__}: {exc}"
    ext = "json" if ds.kind == "json" else "csv"
    if body:
        (out_dir / f"{name}.{ext}").write_bytes(body)
    return {"dataset": name, "status": status, "bytes": len(body), "error": err,
            "seconds": round(time.time() - t0, 2), "url": url}


def yahoo(symbols: list[str], out_dir: Path) -> list[dict]:
    import yfinance as yf

    from apps.api.desk.bars import full_sessions, rows_from_yf, session_bars, split_sessions
    from apps.api.desk.market.session import NSE

    out = []
    now = datetime.now(timezone.utc)
    for sym in symbols:
        df = yf.download(f"{sym}.NS", start=now - timedelta(days=59), interval="5m",
                         auto_adjust=False, progress=False, threads=False)
        rows = rows_from_yf(df)
        bars, dropped = session_bars(rows, NSE, now)
        days = split_sessions(bars)
        full, short = full_sessions(days, "9999-12-31", NSE)
        first = rows[0][0].astimezone(NSE.tz).strftime("%H:%M") if rows else None
        newest = max((r[0] for r in rows), default=None)
        delay = round((now - newest).total_seconds() / 60, 1) if newest else None
        out.append({"symbol": sym, "rows": len(rows), "full_sessions": len(full),
                    "short_sessions": short[-5:], "first_bar_local": first,
                    "dropped": dropped, "newest_bar_minutes_ago": delay})
        if not df.empty:
            df.to_csv(out_dir / f"yahoo-{sym}-60d5m.csv")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--gap-minutes", type=float, default=10)
    ap.add_argument("--only", default="")
    ap.add_argument("--yahoo", default="")
    ap.add_argument("--label", default=socket.gethostname())
    a = ap.parse_args(argv)
    names = [n for n in a.only.split(",") if n] or list(datasets.CATALOG)
    out_dir = ROOT / "tests" / "fixtures" / "desk" / "spike" / a.label
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"host": a.label, "started": datetime.now(timezone.utc).isoformat(), "rounds": []}
    for r in range(a.rounds):
        if r:
            print(f"... waiting {a.gap_minutes} min before round {r + 1}", flush=True)
            time.sleep(a.gap_minutes * 60)
        results = [probe(n, out_dir) for n in names]
        report["rounds"].append(results)
        for x in results:
            ok = "OK " if x["status"] == 200 else "ERR"
            print(f"[{ok}] {x['dataset']:<24} {x['bytes']:>9} B {x['seconds']:>5}s  "
                  f"{x['error'] or ''}", flush=True)
    if a.yahoo:
        report["yahoo"] = yahoo([s.strip().upper() for s in a.yahoo.split(",") if s.strip()], out_dir)
        print(json.dumps(report["yahoo"], indent=2))
    verdict = {}
    for n in names:
        statuses = [x["status"] for rnd in report["rounds"] for x in rnd if x["dataset"] == n]
        verdict[n] = "server" if statuses and all(s == 200 for s in statuses) else "needs helper"
    report["verdict"] = verdict
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print("\nRoute verdict from this machine:")
    for n, v in verdict.items():
        print(f"  {n:<24} {v}")
    print(f"\nSaved responses + report.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
