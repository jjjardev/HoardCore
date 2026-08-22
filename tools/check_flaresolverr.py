#!/usr/bin/env python3
"""Health check for a local FlareSolverr instance.

Verifies the solver can actually launch Chrome and fetch a real page — not
just that its HTTP API answers (a wedged container answers / while every
solve times out). Exits 0 healthy / 1 degraded / 2 unreachable, so it can
gate CI or a cron alert.

Usage:
    python tools/check_flaresolverr.py [--url http://localhost:8191] [--timeout 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _post_solve(base_url: str, url: str, timeout: int) -> tuple[float, str | None]:
    """POST one request.get; returns (elapsed_seconds, error_tag_or_None)."""
    payload = json.dumps({"cmd": "request.get", "url": url,
                          "maxTimeout": timeout * 1000}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1", data=payload,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if "ERR_NAME_NOT_RESOLVED" in body:
            return time.time() - t0, "DNS_FAIL inside container"
        if "cannot connect to chrome" in body.lower():
            return time.time() - t0, "Chrome failed to start"
        if "Timeout" in body:
            return time.time() - t0, f"solve timeout ({e.code})"
        return time.time() - t0, f"HTTP {e.code}: {body[:120]}"
    except Exception as e:
        return time.time() - t0, f"unreachable: {e}"
    sol = data.get("solution") or {}
    if sol.get("status") == 200:
        return time.time() - t0, None
    return time.time() - t0, f"solver returned status {sol.get('status')}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8191")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    # Phase 1: API reachable?
    try:
        with urllib.request.urlopen(args.url, timeout=5) as r:
            info = json.loads(r.read())
        print(f"API up   : {info.get('msg')} (v{info.get('version')})")
    except Exception as e:
        print(f"UNREACHABLE: {e}")
        return 2

    # Phase 2: a trivial, challenge-free page — proves Chrome + egress.
    elapsed, err = _post_solve(args.url, "https://example.com/", args.timeout)
    if err:
        print(f"DEGRADED : example.com solve failed in {elapsed:.1f}s -> {err}")
        print("  Container is up but cannot serve solves. Check:")
        print("  sudo docker logs flaresolverr --tail 30")
        return 1
    print(f"Solve OK : example.com 200 in {elapsed:.1f}s")

    # Phase 3 (optional signal): DuckDuckGo HTML — commonly IP-challenged;
    # success here means the solver clears anti-bot pages end to end.
    elapsed, err = _post_solve(
        args.url, "https://html.duckduckgo.com/html/?q=hoardcore+health+check",
        args.timeout)
    if err:
        print(f"WARN     : DDG solve failed ({err}) — the only built-in discovery "
              "engine is unreachable; use harness-side search + `ingest --urls`.")
        return 1
    print(f"DDG OK   : solved in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
