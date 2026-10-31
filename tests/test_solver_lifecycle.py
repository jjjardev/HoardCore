"""Lifecycle tests for FlareSolverr browser-session release.

A reused solver session holds a headless Chromium context ON THE SOLVER HOST
and lives server-side — it outlives the HoardCore process unless explicitly
destroyed via `sessions.destroy`. These tests pin the release contract:
`aclose()` destroys the session, and the CLI's central sweep reaches every
live fetcher on every exit path.
"""

import asyncio

import hoardcore as hc
from tests.conftest import TempConfig


class _FakeSolverResp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._data

    def __init__(self, data):
        self._data = data


class _RecordingSolver:
    """Stand-in for aiohttp.ClientSession that records solver commands."""

    def __init__(self, handler):
        self.handler = handler
        self.commands: list[dict] = []

    def __call__(self, *a, **k):
        outer = self

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def post(self, url, json=None, timeout=None):  # noqa: A002
                outer.commands.append(dict(json or {}))
                return _FakeSolverResp(outer.handler(json or {}))

        return _S()


class _FakeHttpSession:
    """Minimal stand-in for the shared aiohttp session (closed flag)."""

    def __init__(self):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.closed = True
        self.close_calls += 1


def _ok_handler(payload):
    cmd = payload.get("cmd")
    if cmd == "sessions.create":
        return {"status": "ok", "session": payload.get("session")}
    return {"status": "ok", "solution": {
        "status": 200, "url": payload.get("url", "https://e.test/p"),
        "headers": {"Content-Type": "text/html"}, "response": "solved"}}


def _solver_fetcher(tmp_path):
    f = hc.NetworkFetcher(TempConfig(str(tmp_path), {"solver.enabled": True}))
    return f


def test_aclose_destroys_solver_session_first(tmp_path, monkeypatch):
    """aclose() is the single release point: the server-side browser session
    is destroyed before the HTTP session is closed."""
    fake = _RecordingSolver(_ok_handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path)

    # A solve creates the reused session.
    asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
    sid = f._solver_session
    assert sid, "a solve must create a reused session under session_reuse"

    # Attach a fake shared HTTP session so we can observe the release order.
    http = _FakeHttpSession()
    f._aio_session = http

    destroys: list[int] = []
    real_close = f.close_solver_session

    async def spy_close():
        destroys.append(http.close_calls)  # record position BEFORE http close
        await real_close()

    monkeypatch.setattr(f, "close_solver_session", spy_close)
    asyncio.run(f.aclose())

    assert [c.get("cmd") for c in fake.commands].count("sessions.destroy") == 1
    assert f._solver_session is None
    assert http.closed and http.close_calls == 1
    # Solver teardown happened while the HTTP session was still open.
    assert destroys == [0]


def test_aclose_is_idempotent(tmp_path, monkeypatch):
    fake = _RecordingSolver(_ok_handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path)
    f._solver_session = "hc-test"
    asyncio.run(f.aclose())
    asyncio.run(f.aclose())
    assert [c.get("cmd") for c in fake.commands].count("sessions.destroy") == 1


def test_session_reused_within_run_destroyed_at_release(tmp_path, monkeypatch):
    """The reuse contract: one sessions.create per run no matter how many
    solves; the destroy happens only at release (aclose), never mid-run."""
    fake = _RecordingSolver(_ok_handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path)
    for _ in range(3):
        text, _b, _c, status = asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
        assert status == 200 and text == "solved"
    creates = [c for c in fake.commands if c.get("cmd") == "sessions.create"]
    assert len(creates) == 1
    assert [c.get("cmd") for c in fake.commands].count("sessions.destroy") == 0
    asyncio.run(f.aclose())
    assert [c.get("cmd") for c in fake.commands].count("sessions.destroy") == 1


def test_central_sweep_reaches_every_live_fetcher(tmp_path, monkeypatch):
    """main()'s finally delegates to _close_live_fetchers(): every registered
    fetcher's solver session is destroyed, whatever the exit path."""
    fake = _RecordingSolver(_ok_handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f1 = _solver_fetcher(tmp_path)
    f2 = _solver_fetcher(tmp_path)
    f1._solver_session = "hc-one"
    f2._solver_session = "hc-two"
    asyncio.run(hc._close_live_fetchers())
    destroyed = [c.get("session") for c in fake.commands
                 if c.get("cmd") == "sessions.destroy"]
    assert sorted(destroyed) == ["hc-one", "hc-two"]
    assert f1._solver_session is None and f2._solver_session is None


def test_sweep_tolerates_dead_fetchers_and_unreachable_solver(tmp_path):
    """A fetcher whose solver is gone (or already half-dead) must never turn
    the exit-path sweep into a crash."""
    f = _solver_fetcher(tmp_path)
    f._solver_session = "hc-gone"

    async def boom():
        raise RuntimeError("solver container stopped")

    f.close_solver_session = boom  # type: ignore[method-assign]
    asyncio.run(hc._close_live_fetchers())  # must not raise
    # And a fetcher with no session at all is a no-op.
    f2 = _solver_fetcher(tmp_path)
    f2._solver_session = None
    asyncio.run(hc._close_live_fetchers())
