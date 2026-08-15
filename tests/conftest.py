"""Shared fixtures for HoardCore tests: isolate the vault in a temp dir."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hoardcore as hc  # noqa: E402


class TempConfig:
    """Minimal stand-in for ConfigManager.

    Uses the real module-level defaults so tests exercise real code paths,
    but redirects the vault/filesystem writes away from the user's data.
    """

    def __init__(self, root: str, overrides: dict | None = None):
        self._root = root
        self._overrides = overrides or {}

    def get(self, path: str, default=None):
        if path in self._overrides:
            return self._overrides[path]
        keys = path.split(".")
        value = {
            "general": {"timeout_seconds": 10, "max_retries": 1, "user_agent": "hctest"},
            "network": {"default_strategy": "fast", "enable_preflight": False},
            "auth": {"cookie_string": ""},
            "solver": {"enabled": False, "url": "http://localhost:8191/v1", "solver_timeout": 60},
            "storage": {"root_dir": self._root, "save_binary": False, "save_raw_html": False},
            "parsers": {"enable_pdf": True, "enable_docx": True, "enable_epub": True, "extract_pdf_tables": True, "enable_pdf_ocr": True},
            "crawler": {"respect_robots": False, "sitemap_limit": 500, "parallel_workers": 2},
            "indexer": {"enable_fts": True, "search_limit": 20, "near_dedup": False, "near_dedup_threshold": 3},
            "embeddings": {"enabled": True, "mode": "sparse", "dense_model": "BAAI/bge-small-en-v1.5", "dim": 64, "mrl_dims": 0, "hybrid_search": True, "top_k": 40, "conf_high_abs": 0.025, "conf_low_abs": 0.013},
            "discovery": {"enabled": True, "max_results": 10, "top_rank": 6, "max_retries": 1, "backoff_seconds": 0.05},
            "research": {"answer_first": True, "filter_low": True},
            "chunking": {"max_tokens": 512, "overlap_tokens": 50, "strategy": "heading"},
            "cache": {"ttl_seconds": 86400},
        }
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value


@pytest.fixture()
def vault(tmp_path):
    """A VaultManager isolated in a temp directory."""
    return hc.VaultManager(TempConfig(str(tmp_path)))


@pytest.fixture()
def make_chunk():
    def _make(text: str, header: str = "Root", url: str = "https://example.test/a") -> hc.Chunk:
        return hc.Chunk(text=text, metadata={"header_path": header, "source": url})
    return _make
