"""Tests for Enforced Provenance: machine-verifying [V]/[E]/[H] claims.

verify_claim uses only the vault (hybrid retrieval) and the EmbeddingsEngine,
so these tests build a minimal HoardCore-like object against the temp vault
without touching the network or the real user config.
"""

import types

import hoardcore as hc


def _make_hc(vault):
    """A minimal HoardCore stand-in exposing just .vault and verify_claim."""
    hc_obj = types.SimpleNamespace(vault=vault)
    # Rebind the unbound methods onto the stand-in.
    hc_obj.verify_claim = hc.HoardCore.verify_claim.__get__(hc_obj, types.SimpleNamespace)
    hc_obj.verify_artifact = hc.HoardCore.verify_artifact.__get__(hc_obj, types.SimpleNamespace)
    return hc_obj


def _index(vault, text: str, url: str = "https://example.test/a", header: str = "Root"):
    chunk = hc.Chunk(text=text, metadata={"header_path": header, "source": url})
    vault.index_document(url, [chunk], {"content_type": "text/html", "quality_score": 0.9})


def test_verified_claim_gets_V(tmp_path):
    vault = hc.VaultManager(_TempConfig(str(tmp_path)))
    _index(vault, "Sugarcane bagasse briquettes are made with cassava flour binder and "
                  "burn longer than regular charcoal at a high heat value.")
    hc_obj = _make_hc(vault)

    r = hc_obj.verify_claim("bagasse briquettes burn longer than regular charcoal", recall=4)
    assert r["verdict"] == "[V]"
    assert r["top_source"] == "https://example.test/a"


def test_unbacked_claim_gets_H(tmp_path):
    vault = hc.VaultManager(_TempConfig(str(tmp_path)))
    _index(vault, "The cat sat on the mat in the garden.")
    hc_obj = _make_hc(vault)

    r = hc_obj.verify_claim("Hoarding uses quantum entanglement to search documents", recall=4)
    assert r["verdict"] == "[H]"
    assert r["overlap"] < 0.35


def test_empty_claim_is_H(tmp_path):
    vault = hc.VaultManager(_TempConfig(str(tmp_path)))
    _index(vault, "anything at all")
    r = _make_hc(vault).verify_claim("   ")
    assert r["verdict"] == "[H]"


def test_verify_artifact_demotes_weak_claims(tmp_path):
    vault = hc.VaultManager(_TempConfig(str(tmp_path)))
    _index(vault, "RapidOCR is used for optical character recognition of scanned pages.")
    hc_obj = _make_hc(vault)

    # Two [V]-tagged claims: one substantiated, one about UFOs (unbacked).
    art = tmp_path / "audit.md"
    art.write_text(
        "- RapidOCR performs OCR on scanned pages [V]\n"
        "- HCRAG communicates with aliens [V]\n"
        "- a plain note without a tag\n",
        encoding="utf-8",
    )
    reports = hc_obj.verify_artifact(str(art), recall=4)
    verdicts = {r["verdict"] for r in reports}
    assert "[V]" in verdicts       # substantiated claim passes
    assert "[H]" in verdicts or "[E]" in verdicts  # unbacked one is demoted
    assert len(reports) == 2       # only [V]-tagged lines are audited


def test_verify_artifact_missing_file_returns_empty(tmp_path):
    vault = hc.VaultManager(_TempConfig(str(tmp_path)))
    assert _make_hc(vault).verify_artifact(str(tmp_path / "nope.md")) == []


class _TempConfig:
    """Minimal ConfigManager stand-in pointing at a temp vault root."""

    def __init__(self, root: str):
        self._root = root

    def get(self, path: str, default=None):
        keys = path.split(".")
        value = {
            "general": {"timeout_seconds": 10, "max_retries": 1, "user_agent": "hctest"},
            "network": {"default_strategy": "fast", "enable_preflight": False},
            "auth": {"cookie_string": ""},
            "solver": {"enabled": False, "url": "http://localhost:8191/v1", "solver_timeout": 60},
            "storage": {"root_dir": self._root, "save_binary": False, "save_raw_html": False},
            "parsers": {"enable_pdf": True, "enable_docx": True, "enable_epub": True, "extract_pdf_tables": True, "enable_pdf_ocr": True},
            "crawler": {"respect_robots": False, "sitemap_limit": 500, "parallel_workers": 2},
            "indexer": {"enable_fts": True, "search_limit": 20},
            "embeddings": {"enabled": True, "dim": 64, "hybrid_search": True, "top_k": 40},
            "discovery": {"enabled": True, "max_results": 10, "top_rank": 6, "max_retries": 1, "backoff_seconds": 0.05},
            "chunking": {"max_tokens": 512, "overlap_tokens": 50, "strategy": "heading"},
            "cache": {"ttl_seconds": 86400},
        }
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
