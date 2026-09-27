"""Tests for the persistent embedding-model cache (download once) and offline mode.

fastembed's own default cache is `<tempdir>/fastembed_cache`, which lives under
/tmp and is wiped on every reboot — the model used to be re-downloaded each boot.
HoardCore now points it at a persistent `~/.cache/hoardcore/models` and supports
an opt-in `embeddings.offline` mode that never touches the network.
"""

import os
import sys
import types

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


class _StubConfig:
    """Config stub exposing only the embeddings keys the engine reads."""

    def __init__(self, **embeddings):
        self._embeddings = embeddings

    def get(self, path, default=None):
        if path.startswith("embeddings."):
            return self._embeddings.get(path.split(".", 1)[1], default)
        return default


def _engine(**embeddings):
    """Build an EmbeddingsEngine in sparse mode (no fastembed/model needed)."""
    base = {"mode": "sparse", "dim": 32}
    base.update(embeddings)
    return hc.EmbeddingsEngine(_StubConfig(**base))


def test_cache_dir_defaults_to_persistent_user_cache(tmp_path, monkeypatch):
    """The default must live under ~/.cache (survives reboots) and be created
    on demand — never fastembed's /tmp default."""
    monkeypatch.setenv("HOME", str(tmp_path))
    eng = _engine()
    expected = os.path.join(str(tmp_path), ".cache", "hoardcore", "models")
    assert eng.cache_dir == expected
    assert os.path.isdir(expected)          # created on demand
    # Not the fastembed default: `<tempdir>/fastembed_cache`.
    assert "fastembed_cache" not in eng.cache_dir


def test_cache_dir_override_is_honored_and_expanded(tmp_path):
    target = tmp_path / "models_here"
    eng = _engine(cache_dir=str(target))
    assert eng.cache_dir == str(target)
    assert target.is_dir()


def test_cache_dir_tilde_is_expanded(tmp_path):
    eng = _engine(cache_dir="~/custom_models")
    assert not eng.cache_dir.startswith("~")
    assert eng.cache_dir.endswith("custom_models")


def test_cache_dir_degrades_gracefully_when_uncreatable(monkeypatch):
    """An un-creatable cache dir must not crash: hand fastembed its own default."""
    _engine()  # sanity: a working filesystem resolves a real path

    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(hc.os, "makedirs", _boom)
    # Re-resolve with a broken filesystem.
    broken = hc.EmbeddingsEngine.__new__(hc.EmbeddingsEngine)
    broken.config = _StubConfig(mode="sparse", dim=32)
    assert broken._model_cache_dir() == ""


def test_load_dense_passes_cache_dir_and_no_offline_flag(monkeypatch, tmp_path):
    """A normal run must pass the persistent cache_dir and stay network-capable
    (local_files_only only under embeddings.offline)."""
    captured = {}

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            captured["model_name"] = model_name
            captured.update(kwargs)
            # 3-dim fake vector so _load_dense probes dim=3.
            self.embed = lambda texts, **kw: iter([[0.1, 0.2, 0.3] for _ in texts])

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    eng = _engine(mode="dense", cache_dir=str(tmp_path / "m"))
    assert eng.dim == 3
    assert eng.mode == "dense"
    assert captured["model_name"] == "BAAI/bge-small-en-v1.5"
    assert captured["cache_dir"] == str(tmp_path / "m")
    assert "local_files_only" not in captured


def test_load_dense_offline_sets_local_files_only(monkeypatch, tmp_path):
    captured = {}

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            captured.update(kwargs)
            self.embed = lambda texts, **kw: iter([[0.1, 0.2, 0.3] for _ in texts])

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    _engine(mode="dense", offline=True, cache_dir=str(tmp_path / "m"))
    assert captured["local_files_only"] is True


def test_offline_missing_model_raises_instead_of_silent_sparse(tmp_path, monkeypatch):
    """Offline + no cached model must FAIL loudly, never silently demote to
    sparse hashing (which would quietly degrade recall quality)."""
    class BoomTextEmbedding:
        def __init__(self, *_a, **_k):
            raise ValueError("Could not find model in cache_dir")

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = BoomTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    with pytest.raises(RuntimeError) as exc:
        _engine(mode="dense", offline=True, cache_dir=str(tmp_path / "empty"))
    assert "offline" in str(exc.value)


def test_online_model_failure_still_falls_back_to_sparse(tmp_path, monkeypatch):
    """Without offline, a failed load keeps the historical sparse fallback."""
    class BoomTextEmbedding:
        def __init__(self, *_a, **_k):
            raise ValueError("network down")

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = BoomTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    eng = _engine(mode="dense", cache_dir=str(tmp_path / "m"))
    assert eng.mode == "sparse"


def test_cache_dir_is_not_part_of_the_vector_fingerprint(tmp_path):
    """Moving the cache must never invalidate stored vectors (no needless
    re-embed of the whole vault)."""
    a = _engine(cache_dir=str(tmp_path / "a"))
    b = _engine(cache_dir=str(tmp_path / "b"))
    assert a.fingerprint() == b.fingerprint()


def test_default_config_documents_the_new_keys():
    """DEFAULT_CONFIG is the template users get; both keys must be discoverable."""
    defaults = hc.ConfigManager._defaults(None)["embeddings"]
    assert "cache_dir" in hc.DEFAULT_CONFIG
    assert "offline" in hc.DEFAULT_CONFIG
    assert defaults["cache_dir"] == ""
    assert defaults["offline"] is False


def test_temp_config_stub_is_unaffected(tmp_path):
    """Sanity: the shared test stub still builds a vault (no dense load)."""
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    assert vault.embeddings.mode in ("dense", "sparse")


def test_concurrent_embeddings_are_serialized(monkeypatch, tmp_path):
    """The embed lock must make at most ONE ONNX forward pass run at a time.

    A single InferenceSession is already internally multi-threaded, so
    concurrent callers only oversubscribe the CPU: the observed failure was a
    27-URL Cloudflare batch pinned at 427% CPU with no forward progress. This
    reproduces that call shape (N threads through the same engine) and asserts
    the model is never entered concurrently.
    """
    import threading
    import time as _time

    state = {"in_flight": 0, "peak": 0}
    guard = threading.Lock()

    class _SlowModel:
        def embed(self, texts, **kw):
            with guard:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            try:
                _time.sleep(0.05)  # hold the "forward pass" open
                return iter([[0.1, 0.2, 0.3] for _ in texts])
            finally:
                with guard:
                    state["in_flight"] -= 1

    class FakeTextEmbedding:
        def __init__(self, *_a, **_k):
            self._m = _SlowModel()

        def embed(self, texts, **kw):
            return self._m.embed(texts, **kw)

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    eng = _engine(mode="dense", cache_dir=str(tmp_path / "m"))
    assert eng.dim == 3

    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            for _ in range(3):
                assert len(eng.vectorize_batch(["a", "b"])) == 2
                assert len(eng.vectorize("c")) > 0
        except BaseException as e:  # noqa: BLE001 - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"worker raised: {errors[0]!r}"
    assert state["peak"] == 1, (
        f"embedder entered concurrently (peak={state['peak']}); the lock is not "
        f"serializing ONNX forward passes"
    )


def test_embed_lock_is_released_on_failure(monkeypatch, tmp_path):
    """A raising model must not wedge the lock and deadlock every later call.

    The model succeeds for the dimension probe that loads the engine, then
    starts raising — the realistic mid-session failure.
    """
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def embed(self, texts, **kw):
            self.calls += 1
            if self.calls == 1:          # the load-time dim probe
                return iter([[0.1, 0.2, 0.3] for _ in texts])
            raise RuntimeError("onnx exploded")

    class FakeTextEmbedding:
        def __init__(self, *_a, **_k):
            self._m = FlakyModel()

        def embed(self, texts, **kw):
            return self._m.embed(texts, **kw)

    fake_mod = types.ModuleType("fastembed")
    fake_mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_mod)

    eng = _engine(mode="dense", cache_dir=str(tmp_path / "m"))
    assert eng.dim == 3
    # Batch embed catches the failure, then the per-item fallback surfaces it.
    with pytest.raises(RuntimeError):
        eng.vectorize_batch(["a"])
    # The critical assertion: the lock was released, so the next caller runs
    # instead of blocking forever on a wedged mutex.
    assert eng._embed_lock.acquire(timeout=5) is True
    eng._embed_lock.release()
