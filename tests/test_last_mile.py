"""Tests for the last four robustness fixes.

1. Sitemap URL canonicalization (a literal `/./` segment hid real pages).
2. Cross-vault chrome demotion resolved in one batched query per vault.
3. FlareSolverr browser-session reuse + multi-endpoint failover.
4. The CLI warns when an artifact is written outside the artifacts dir.
"""

import asyncio

import hoardcore as hc
from tests.conftest import TempConfig

# --------------------------------------------------------------------------
# 1. Sitemap URL canonicalization
# --------------------------------------------------------------------------

def test_normalize_url_repairs_dot_segments():
    N = hc.CrawlerPlanner._normalize_url
    # The live ASTI case: a literal '/./' segment, whose canonical form is a
    # page the crawl would otherwise never discover.
    assert N("https://e.test/./projects/ulat/") == "https://e.test/projects/ulat/"
    assert N("https://e.test/projects/ulat/") == N("https://e.test/./projects/ulat/")
    assert N("https://e.test//a//b//") == "https://e.test/a/b/"
    assert N("https://e.test/a/../b/c") == "https://e.test/b/c"
    assert N("https://e.test/a/b/..") == "https://e.test/a"
    # A fragment never reaches the server and would split one page in two.
    assert N("https://e.test/page#frag") == "https://e.test/page"
    # Default ports are noise.
    assert N("http://e.test:80/x") == "http://e.test/x"
    assert N("https://e.test:443/y") == "https://e.test/y"
    # Root survives.
    assert N("https://e.test/") == "https://e.test/"


def test_normalize_url_preserves_query_byte_for_byte():
    """A dot segment or slash inside the query can be meaningful, and a
    WordPress image URL hides behind `?bwg=` — normalizing either would corrupt
    the target."""
    N = hc.CrawlerPlanner._normalize_url
    q = "https://e.test/img.jpg?bwg=1783321955"
    assert N(q) == q
    assert N("https://e.test/p?a=./b&c=../d") == "https://e.test/p?a=./b&c=../d"


def test_normalize_url_leaves_non_absolute_input_alone():
    N = hc.CrawlerPlanner._normalize_url
    assert N("not a url") == "not a url"
    assert N("") == ""
    assert N("/relative/path") == "/relative/path"


def test_sitemap_canonicalizes_before_dedupe_and_budget(tmp_path, monkeypatch):
    """The malformed spelling must collapse onto the canonical URL *before*
    dedupe, so a page listed both ways costs one fetch, not two — and only the
    canonical URL is stored."""
    bodies = {
        "https://e.test/post-sitemap.xml":
            "<urlset>"
            "<url><loc>https://e.test/./projects/ulat/</loc></url>"
            "<url><loc>https://e.test/projects/ulat/</loc></url>"
            "<url><loc>https://e.test//deep//path/</loc></url>"
            "</urlset>",
    }
    planner = _sitemap_planner(tmp_path, bodies)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", _sitemap_session(bodies))
    urls = asyncio.run(planner.parse_sitemap("https://e.test/post-sitemap.xml"))
    assert urls == ["https://e.test/projects/ulat/", "https://e.test/deep/path/"]


# --------------------------------------------------------------------------
# 2. Cross-vault chrome demotion
# --------------------------------------------------------------------------

def test_cross_vault_chrome_check_is_batched_per_vault(tmp_path):
    """The demotion must issue ONE ledger query per vault, not one per chunk:
    per-chunk queries turned a 2-vault recall into dozens of pool acquires."""
    nav = ("RESEARCH Space Technology Wireless Technology Artificial Intelligence "
           "SMART Cities Emerging Technologies PhilSensors")
    body = ("Asticon 2026 showcases microsatellite telemetry pipelines for "
            "hydromet deployments.")

    vaults = []
    for name in ("alpha", "beta"):
        cfg = TempConfig(str(tmp_path / name))
        v = hc.VaultManager(cfg, name)
        for i in range(3):
            v.index_document(
                f"https://{name}.test/p{i}",
                [hc.Chunk(text=nav, metadata={"header_path": "Menu",
                                              "source": f"https://{name}.test/p{i}"})], {})
        v.index_document(
            f"https://{name}.test/real",
            [hc.Chunk(text=body, metadata={"header_path": "Body",
                                           "source": f"https://{name}.test/real"})], {})
        vaults.append(v)

    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(str(tmp_path / "alpha"))
    inst.bus = hc.EventBus()
    inst.vault = vaults[0]
    inst.vaults = vaults

    calls: list[int] = []
    for v in vaults:
        original = v._chrome_hashes

        def _counting(cur, texts, _orig=original, _calls=calls):
            _calls.append(len(texts))
            return _orig(cur, texts)

        v._chrome_hashes = _counting

    hits = inst._search_across_vaults("ASTICON microsatellite telemetry", limit=6)
    assert hits
    # The per-chunk implementation issued one ledger query PER CHUNK (every call
    # carrying a single text); the batched one always passes the whole group.
    singles = [n for n in calls if n == 1]
    assert not singles, f"per-chunk ledger queries detected: {calls}"
    body_idx = next(i for i, h in enumerate(hits) if "microsatellite" in h.text)
    nav_idx = [i for i, h in enumerate(hits) if h.text == nav]
    assert nav_idx and all(i > body_idx for i in nav_idx)


# --------------------------------------------------------------------------
# 3. FlareSolverr session reuse + multi-endpoint failover
# --------------------------------------------------------------------------

class _FakeSolverResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _RecordingSolverSession:
    """Captures every command posted to the solver, per endpoint."""

    def __init__(self, endpoints, handler):
        self.endpoints = endpoints
        self.handler = handler
        self.commands: list[tuple[str, dict]] = []

    def __call__(self, *a, **k):
        outer = self

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def post(self, url, json=None, timeout=None):  # noqa: A002 - aiohttp kwarg name
                outer.commands.append((url, dict(json or {})))
                return _FakeSolverResp(outer.handler(url, json or {}))

        return _S()


class _SitemapResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


def _sitemap_session(bodies):
    class _S:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, timeout=None, **kwargs):
            body = bodies.get(url)
            return _SitemapResp(200, body) if body is not None else _SitemapResp(404, "")
    return _S


def _sitemap_planner(tmp_path, bodies):
    cfg = TempConfig(str(tmp_path), overrides={"crawler.respect_robots": False})
    planner = hc.CrawlerPlanner(cfg)
    hc.NetworkFetcher.validate_url_target = staticmethod(lambda _u: True)
    return planner


def _solver_fetcher(tmp_path, **overrides):
    base = {
        "general.timeout_seconds": 10,
        "solver.enabled": True,
        "solver.url": "http://localhost:8191/v1",
        "solver.solver_timeout": 5,
        "network.ssrf_protection": False,
    }
    base.update(overrides)
    return hc.NetworkFetcher(TempConfig(str(tmp_path), overrides=base))


def test_session_is_created_once_and_reused_for_every_solve(tmp_path, monkeypatch):
    """The whole point: without a reused session each request.get re-pays the
    Cloudflare challenge (~50 s per URL on a protected site)."""
    def handler(_url, payload):
        if payload.get("cmd") == "sessions.create":
            return {"status": "ok", "session": payload.get("session")}
        return {"status": "ok", "solution": {
            "status": 200, "url": payload["url"],
            "headers": {"Content-Type": "text/html"},
            "response": "<html>body</html>"}}

    fake = _RecordingSolverSession(None, handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)

    f = _solver_fetcher(tmp_path)
    for i in range(4):
        text, _b, _c, status = asyncio.run(f._fetch_flaresolverr(f"https://e.test/p{i}"))
        assert status == 200 and "body" in text

    creates = [c for _u, c in fake.commands if c.get("cmd") == "sessions.create"]
    gets = [c for _u, c in fake.commands if c.get("cmd") == "request.get"]
    assert len(creates) == 1, "the session must be created exactly once per run"
    assert len(gets) == 4
    assert all(g.get("session") for g in gets), "every solve must carry the session"
    # One distinct session id across the batch.
    assert len({g["session"] for g in gets}) == 1


def test_session_reuse_can_be_disabled(tmp_path, monkeypatch):
    def handler(_url, payload):
        if payload.get("cmd") == "sessions.create":
            return {"status": "ok", "session": payload.get("session")}
        return {"status": "ok", "solution": {
            "status": 200, "url": payload["url"],
            "headers": {"Content-Type": "text/html"}, "response": "x"}}

    fake = _RecordingSolverSession(None, handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path, **{"solver.session_reuse": False})
    asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
    assert not [c for _u, c in fake.commands if c.get("cmd") == "sessions.create"]
    assert not fake.commands[0][1].get("session")


def test_rejected_session_falls_back_to_stateless_once(tmp_path, monkeypatch):
    """A session that expires or was dropped must not poison the rest of the
    batch: the URL is retried statelessly and the session is abandoned."""
    state = {"rejects": 1}

    def handler(_url, payload):
        if payload.get("cmd") == "sessions.create":
            return {"status": "ok", "session": payload.get("session")}
        if payload.get("cmd") == "sessions.destroy":
            return {"status": "ok"}
        if payload.get("session") and state["rejects"]:
            state["rejects"] -= 1
            return {"status": "error", "message": "Session not found"}
        return {"status": "ok", "solution": {
            "status": 200, "url": payload["url"],
            "headers": {"Content-Type": "text/html"}, "response": "recovered"}}

    fake = _RecordingSolverSession(None, handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path)
    text, _b, _c, status = asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
    assert status == 200 and "recovered" in text
    # The retry carried no session.
    gets = [c for _u, c in fake.commands if c.get("cmd") == "request.get"]
    assert len(gets) == 2 and "session" not in gets[1]
    # And the dead session is not reused afterwards.
    assert f._solver_session is None


def test_close_destroys_the_session(tmp_path, monkeypatch):
    def handler(_url, payload):
        if payload.get("cmd") == "sessions.create":
            return {"status": "ok", "session": payload.get("session")}
        return {"status": "ok", "solution": {
            "status": 200, "url": payload["url"],
            "headers": {"Content-Type": "text/html"}, "response": "x"}}

    fake = _RecordingSolverSession(None, handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path)
    asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
    asyncio.run(f.close_solver_session())
    destroys = [c for _u, c in fake.commands if c.get("cmd") == "sessions.destroy"]
    assert len(destroys) == 1
    assert f._solver_session is None
    # Closing twice must not re-destroy.
    asyncio.run(f.close_solver_session())
    assert len([c for _u, c in fake.commands
                if c.get("cmd") == "sessions.destroy"]) == 1


def test_secondary_endpoint_takes_over_when_primary_is_dead(tmp_path, monkeypatch):
    """One dead FlareSolverr container must degrade throughput, not fail every
    fetch — and the fetch still succeeds through the healthy endpoint."""
    good = "http://second:8191/v1"

    def handler(url, payload):
        if "second" not in url:
            raise ConnectionRefusedError("primary is down")
        if payload.get("cmd") == "sessions.create":
            return {"status": "ok", "session": payload.get("session")}
        return {"status": "ok", "solution": {
            "status": 200, "url": payload["url"],
            "headers": {"Content-Type": "text/html"}, "response": "from second"}}

    fake = _RecordingSolverSession(None, handler)
    monkeypatch.setattr(hc.aiohttp, "ClientSession", fake)
    f = _solver_fetcher(tmp_path, **{"solver.urls": good})
    text, _b, _c, status = asyncio.run(f._fetch_flaresolverr("https://e.test/p"))
    assert status == 200 and "from second" in text
    attempted = [u for u, _c in fake.commands]
    assert attempted[0] == "http://localhost:8191/v1", attempted
    assert good in attempted
    # Both endpoints are attempted, in order; the healthy one answers.
    gets = [u for u, c in fake.commands if c.get("cmd") == "request.get"]
    assert gets == ["http://localhost:8191/v1", good], gets


def test_solver_urls_appends_to_the_primary_never_replaces_it(tmp_path):
    """`solver.url` is always tried first; `urls` only adds fallbacks, so an
    existing single-endpoint config cannot be silently orphaned."""
    f = _solver_fetcher(tmp_path)
    assert f._solver_urls == ["http://localhost:8191/v1"]
    f2 = _solver_fetcher(tmp_path, **{"solver.urls": " a:1/v1 , b:2/v1 c:3/v1 "})
    assert f2._solver_urls == ["http://localhost:8191/v1", "a:1/v1", "b:2/v1", "c:3/v1"]
    # A duplicate of the primary is not listed twice.
    f3 = _solver_fetcher(tmp_path, **{"solver.urls": "http://localhost:8191/v1"})
    assert f3._solver_urls == ["http://localhost:8191/v1"]


# --------------------------------------------------------------------------
# 4. CLI warns about artifacts written outside artifacts/
# --------------------------------------------------------------------------

def test_resolve_artifact_out_leaves_external_paths_alone(tmp_path):
    """Documented behaviour: a path outside the artifacts dir is honoured
    verbatim (the caller keeps full control), which is exactly why the CLI has
    to warn about it."""
    cfg = TempConfig(str(tmp_path))
    cfg._overrides["storage.artifacts_dir"] = str(tmp_path / "artifacts")
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = cfg
    inst.vault = hc.VaultManager(cfg)
    assert inst.artifacts_dir == str(tmp_path / "artifacts")
    outside = str(tmp_path / "scratch.md")
    assert inst.resolve_artifact_out(outside) == outside
    # A path INSIDE the artifacts dir is re-scoped into the day folder, so it
    # stays put across runs; one outside is left exactly as asked.
    inside = str(tmp_path / "artifacts" / "report.md")
    resolved = inst.resolve_artifact_out(inside)
    assert resolved.endswith("report.md")
    assert str(tmp_path / "artifacts") in resolved
