#!/usr/bin/env python3
"""
HoardCore v0.8.3 - Research toolkit for AI agents: retrieval & deep research.
Ingests HTML, PDF, DOCX, EPUB, and TXT into a persistent, searchable SQLite Vault.
Hybrid retrieval fuses FTS5 keyword search with vector search (RRF), and a
web-discovery action feeds the crawler from a live search query.

Usage:
    python hoardcore.py <URL> --action scrape|crawl|search --query "text"
    python hoardcore.py _ --action ingest --urls "u1,u2,u3"
    python hoardcore.py _ --action discover --query "negros occidental renewable energy" --limit 5
    python hoardcore.py _ --action search --query "solar" --mode fast   # FTS-only
    python hoardcore.py _ --action search --query "solar" --mode hybrid # force vector+RRF
    python hoardcore.py _ --action check --migrate  # rebuild vault at 16 KB pages
"""
from __future__ import annotations

__version__ = "0.8.4"

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import tomllib
import zipfile
from array import array
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

# --- GUARANTEED DEPENDENCIES (Installed via Makefile) ---
import aiohttp
import trafilatura
from aiohttp import ClientTimeout, TCPConnector
from readability import Document

# Heavy binary parsers are lazy-imported so that HTML/text-only usage works
# even without PDF/DOCX/EPUB libraries installed. Modules are fetched on first
# use in DocumentParser (see _import_binary_parsers) and cached.
FITZ_AVAILABLE = False
DOCX_AVAILABLE = False
EPUB_AVAILABLE = False
RAPIDOCR_AVAILABLE = False
_BINARY_IMPORTED = False

# numpy is optional but strongly recommended: it makes the brute-force cosine
# scan 50-100x faster than the pure-Python fallback (B1). fastembed pulls it in
# transitively, but sparse-mode-only installs can live without it.
try:
    import numpy as _np
    NP_AVAILABLE = True
except ImportError:
    _np = None
    NP_AVAILABLE = False

# curl_cffi is optional but highly recommended (installed via Makefile)
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    CURL_AVAILABLE = False
    print("Warning: curl_cffi not found. Advanced TLS impersonation disabled.", file=sys.stderr)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("hoardcore")

# =============================================================================
# 1. DATA MODELS
# =============================================================================

@dataclass
class Chunk:
    """A semantic chunk of text ready for LLM injection."""
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "metadata": self.metadata}

# =============================================================================
# 2. CONFIGURATION MANAGER
# =============================================================================

DEFAULT_CONFIG = """
# HoardCore v0.8.1 Configuration

[general]
timeout_seconds = 30
max_retries = 2
user_agent = "HoardCore-Bot/5.0 (LLM Agent)"

[network]
default_strategy = "aggressive"   # fast, balanced, aggressive
enable_preflight = true

[auth]
cookie_string = ""

[solver]
enabled = false
url = "http://localhost:8191/v1"
solver_timeout = 60

[storage]
root_dir = "hoardcore_data"
artifacts_dir = "artifacts"          # Finished research deliverables (reports, syntheses, audits)
artifacts_by_day = true          # Organize deliverables into artifacts/YYYY-MM-DD/ subfolders
save_binary = true               # Save original PDF/DOCX/EPUB files
save_raw_html = false            # Save raw HTML for debugging
page_size = 16384                # SQLite page size (bytes). 16 KB keeps 384-dim
                                 # vectors inline (no overflow pages): ~1.7x faster
                                 # vector lookups than the 4 KB default. Applies
                                 # to new vaults; migrate existing ones with
                                 # `--action check --migrate`.

[parsers]
enable_pdf = true
enable_docx = true
enable_epub = true
extract_pdf_tables = true
enable_pdf_ocr = true            # auto-OCR scanned/image-only PDF pages (needs rapidocr_onnxruntime)

[crawler]
respect_robots = true
sitemap_limit = 500
parallel_workers = 5

[indexer]
enable_fts = true
search_limit = 20
# parallel = false        # threaded ingest for large batches (off by default)
near_dedup = false        # simhash near-duplicate chunk filter (off: preserves
                          # cross-source corroborating text as evidence)
near_dedup_threshold = 3  # hamming-distance cutoff for a near-dup block (0-64)

[embeddings]
enabled = true
mode = "dense"           # dense = ONNX sentence-transformer (default); sparse = lightweight hash fallback
dense_model = "BAAI/bge-small-en-v1.5"
dim = 256                # used in sparse mode; dense uses the model's dimension
mrl_dims = 0             # Matryoshka truncation: store only the first N dims of
                         # dense vectors (0 = keep the full model dim). Shrinks
                         # the vector table ~4x at 384->96; best on MRL-trained
                         # models. Existing rows rebuild via backfill.
hybrid_search = true       # merge FTS + vector via RRF
top_k = 40                 # candidate pool from vector search
quantize = "float32"       # "float32" (default) or "int8" (1 byte/dim, ~4x smaller, tiny recall cost)
fts_fast_path = true       # skip the vector scan when FTS5 alone fills the result set (all-term AND match)
recency_half_life_days = 0 # recency weighting in RRF: 0 = disabled; e.g. 30 halves an old hit's score per month

[research]
answer_first = true      # skip live DISCOVER when the existing vault already
                         # returns a high-confidence hit (Adaptive-RAG style:
                         # most recurring questions need no new retrieval)
filter_low = true        # at EMIT, drop confidence='low' chunks whenever
                         # stronger (non-low) chunks remain

[discovery]
enabled = true
provider = "duckduckgo_html"   # free HTML endpoint; uses the existing fetch/FlareSolverr chain (Mojeek auto-fallback)
max_results = 10
top_rank = 6                   # ingest only the top-N ranked results
max_retries = 2                # per-provider transient-failure retries
backoff_seconds = 1.5          # exponential backoff base

[chunking]
max_tokens = 512
overlap_tokens = 50
strategy = "heading"             # heading or paragraph

[cache]
ttl_seconds = 86400              # 24 hours
"""

class ConfigManager:
    CONFIG_PATH = "hoardcore.toml"
    _instance = None
    _config: dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._load()
            self._initialized = True

    def _load(self) -> None:
        if not os.path.exists(self.CONFIG_PATH):
            logger.info(f"Creating default config: {self.CONFIG_PATH}")
            with open(self.CONFIG_PATH, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_CONFIG.strip())

        try:
            with open(self.CONFIG_PATH, 'rb') as f:
                self._config = tomllib.load(f)
        except Exception as e:
            logger.warning(f"Config parse error: {e}. Using defaults.")
            self._config = self._defaults()

        defaults = self._defaults()
        for key, value in defaults.items():
            if key not in self._config:
                self._config[key] = value
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in self._config[key]:
                        self._config[key][sub_key] = sub_value

    def _defaults(self) -> dict[str, Any]:
        return {
            "general": {"timeout_seconds": 30, "max_retries": 2, "user_agent": "HoardCore/5.0"},
            "network": {"default_strategy": "aggressive", "enable_preflight": True},
            "auth": {"cookie_string": ""},
            "solver": {"enabled": False, "url": "http://localhost:8191/v1", "solver_timeout": 60},
            "storage": {"root_dir": "hoardcore_data", "artifacts_dir": "artifacts", "artifacts_by_day": True, "save_binary": True, "save_raw_html": False, "page_size": 16384},
            "parsers": {"enable_pdf": True, "enable_docx": True, "enable_epub": True, "extract_pdf_tables": True, "enable_pdf_ocr": True},
            "crawler": {"respect_robots": True, "sitemap_limit": 500, "parallel_workers": 5},
            "indexer": {"enable_fts": True, "search_limit": 20, "parallel": False,
                        "near_dedup": False, "near_dedup_threshold": 3},
            "embeddings": {"enabled": True, "mode": "dense", "dense_model": "BAAI/bge-small-en-v1.5", "dim": 256, "mrl_dims": 0, "hybrid_search": True, "top_k": 40, "conf_high_abs": 0.025, "conf_low_abs": 0.020, "quantize": "float32", "fts_fast_path": True, "recency_half_life_days": 0},
            "research": {"answer_first": True, "filter_low": True},
            "discovery": {"enabled": True, "provider": "duckduckgo_html", "max_results": 10, "top_rank": 6, "max_retries": 2, "backoff_seconds": 1.5},
            "chunking": {"max_tokens": 512, "overlap_tokens": 50, "strategy": "heading"},
            "cache": {"ttl_seconds": 86400}
        }

    def get(self, path: str, default: Any = None) -> Any:
        keys = path.split('.')
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

# =============================================================================
# 2.5 EMBEDDING ENGINE (Vector basis for hybrid retrieval)
# =============================================================================

def _fnv1a(data: bytes) -> int:
    """FNV-1a 64-bit hash. Deterministic feature hashing for the sparse fallback."""
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def hamming64(a: int, b: int) -> int:
    """Popcount of the XOR of two 64-bit hashes (simhash near-dup distance)."""
    return bin(a ^ b).count("1")


class EmbeddingsEngine:
    """Turns chunk text into fixed-dimension vectors for hybrid retrieval.

    Default mode is dense: an ONNX-quantized sentence-transformer embedding
    (via fastembed on onnxruntime, no PyTorch) that captures semantic
    similarity. A lightweight sparse fallback (FNV-1a feature hashing of word
    + char n-gram features into a unit vector) remains for environments where
    the dense model is unavailable. Both fuse with FTS5 keyword search via
    Reciprocal Rank Fusion, keeping HoardCore fast and single-file.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.enabled = config.get('embeddings.enabled', True)
        # Mode: "dense" (default, ONNX-quantized sentence-transformer via
        # fastembed) or "sparse" (dependency-light FNV-1a lexical hash). Dense
        # is lazily loaded and falls back to sparse if fastembed is missing.
        self.mode = str(config.get('embeddings.mode', 'dense')).lower()
        self.dim = int(config.get('embeddings.dim', 256))
        # Matryoshka-style dimension truncation (MRL): embed at full model
        # dim, then STORE only the first `mrl_dims` dimensions (models trained
        # for MRL like nomic/mxbai retain most quality when truncated; e.g.
        # 93% retention at 12x compression on OpenAI's text-embedding-3-large).
        # Queries are truncated the same way so cosine stays consistent; the
        # existing backfill dimension-migration rebuilds any stale rows.
        self.mrl_dims = int(config.get('embeddings.mrl_dims', 0) or 0)
        self.base_dim = self.dim
        # Vector storage format: "float32" (4 bytes/dim) or "int8" (1 byte/dim,
        # ~4x smaller vault, tiny recall cost). int8 is applied to dense output
        # only — the sparse hash already produces compact normalized vectors.
        self.quantize = str(config.get('embeddings.quantize', 'float32')).lower()
        if self.mode == 'dense' and self.quantize not in ('float32', 'int8'):
            logger.warning(
                f"Unrecognized quantize={self.quantize!r}; falling back to float32."
            )
            self.quantize = 'float32'
        self._dense = None  # lazy fastembed backend (model + dim)
        if self.mode == 'dense':
            try:
                self.dim = self._load_dense()
                self.base_dim = self.dim
            except Exception as e:  # fastembed missing or model download failed
                logger.warning(
                    f"Dense mode requested but unavailable ({e}); "
                    f"falling back to sparse lexical hashing."
                )
                self.mode = 'sparse'
        if self.mode == 'dense' and self.mrl_dims > 0 and self.mrl_dims < self.base_dim:
            self.dim = self.mrl_dims
            logger.info(
                f"MRL dim truncation active: storing first {self.dim} of "
                f"{self.base_dim} embedding dims."
            )

    @property
    def bytes_per_dim(self) -> int:
        """On-disk bytes per embedding dimension (4 for float32, 1 for int8)."""
        return 1 if (self.mode == 'dense' and self.quantize == 'int8') else 4

    def _load_dense(self) -> int:
        """Lazily import fastembed and load the ONNX-quantized model.

        Returns the model's embedding dimension. Raises if fastembed is not
        installed or the model cannot be loaded, so the caller can fall back
        to sparse.
        """
        from fastembed import TextEmbedding

        model_name = str(self.config.get('embeddings.dense_model',
                                         'BAAI/bge-small-en-v1.5'))
        model = TextEmbedding(model_name)
        # Probe a short real token to discover the embedding dimension (an empty
        # string can produce a degenerate vector on some tokenizers).
        probe = next(iter(model.embed(['probe'])), None)
        dim = len(probe) if probe is not None else 384
        if dim <= 0:
            # Fall back to the documented MiniLM dimension if probe is empty.
            dim = 384
        self._dense = (model, dim)
        logger.info(f"Dense embeddings loaded: {model_name} (dim={dim})")
        return dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        """Word unigrams + 3-gram shingles for sparse lexical features."""
        words = re.findall(r"[a-zà-ÿ0-9']+", text.lower())
        shingles: list[str] = []
        for w in words:
            if len(w) <= 3:
                shingles.append(w)
                continue
            for i in range(len(w) - 2):
                shingles.append(w[i:i + 3])
        return words + shingles

    def _hash_vector(self, text: str) -> bytes:
        """Feature-hash tokens into an L2-normalized float32 vector."""
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for t in self._tokens(text):
            counts[t] = counts.get(t, 0) + 1
        for token, n in counts.items():
            digest = _fnv1a(token.encode('utf-8'))
            idx = digest % self.dim
            sign = 1.0 if (digest >> 62) & 1 else -1.0
            vec[idx] += sign * (1.0 + n ** 0.5)
        norm = (sum(v * v for v in vec)) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        arr = array('f', vec)
        return arr.tobytes()

    def vectorize(self, text: str) -> bytes:
        vec = self._vectorize_dense(text) if (
            self.mode == 'dense' and self._dense is not None
        ) else self._hash_vector(text)
        if self.mode == 'dense' and self.mrl_dims > 0:
            vec = self._truncate_f32(vec, self.dim)
        if self.mode == 'dense' and self.quantize == 'int8' and vec:
            return self._quantize_int8(vec)
        return vec

    @staticmethod
    def _truncate_f32(vec: bytes, dims: int) -> bytes:
        """Slice a float32 vector blob to its first `dims` dimensions.

        Matryoshka-style truncation for models trained for progressive dim
        reduction (MRL). Query and stored vectors are truncated identically so
        the cosine dimension check stays consistent. Empty/invalid payloads
        pass through unchanged.
        """
        if not vec:
            return vec
        arr = array('f')
        arr.frombytes(vec)
        if len(arr) <= dims:
            return vec
        return array('f', arr[:dims]).tobytes()

    @staticmethod
    def _quantize_int8(vec: bytes) -> bytes:
        """Convert a float32 vector to signed int8 (scale to [-127, 127]).

        Dequantization for cosine is just dividing back by 127, so vectors stay
        comparable without a per-vector scale factor.
        """
        arr = array('f')
        arr.frombytes(vec)
        q = array('b', (max(-127, min(127, round(v * 127))) for v in arr))
        return q.tobytes()

    def _vectorize_dense(self, text: str) -> bytes:
        """Encode text with the loaded dense model, stored as float32 bytes.

        The query is embedded, the result is normalized to a unit vector, and
        the float32 payload is returned in the same binary layout as the
        sparse hash so the existing cosine path works unchanged.
        """
        from array import array

        model, dim = self._dense
        vec = next(iter(model.embed([text])), None)
        if vec is None:
            return b""
        if _np is not None:
            arr = _np.asarray(vec, dtype=_np.float32).reshape(-1)
            norm = float(_np.linalg.norm(arr))
            if norm > 0:
                arr = arr / norm
            return array('f', arr).tobytes()
        # Pure-Python fallback (no numpy installed).
        arr = list(vec)
        norm = float(sum(v * v for v in arr)) ** 0.5
        if norm > 0:
            arr = [v / norm for v in arr]
        return array('f', arr).tobytes()

    @staticmethod
    def cosine(a: bytes, b: bytes, dim: int) -> float:
        """Cosine similarity between two serialized vectors.

        Vectors are stored L2-normalized at build time, so a plain dot product
        equals cosine. int8-quantized vectors are exactly `dim` bytes; float32
        are `dim * 4`. Any other payload length signals corruption or a stale
        embedding format, and is surfaced instead of silently truncated (A7),
        so mismatches are never hidden by a plausible-but-wrong score.
        """
        int8_desc = (dim, dim)
        f32_desc = (dim * 4, dim * 4)
        if (len(a), len(b)) != int8_desc and (len(a), len(b)) != f32_desc:
            logger.warning(
                "vector dim mismatch: query=%d bytes, stored=%d bytes, "
                "expected int8=%d or float32=%d; scoring as 0.0",
                len(a), len(b), dim, dim * 4,
            )
            return 0.0
        if len(a) == dim:  # int8 path
            if _np is not None:
                # Must upcast to int32, NOT int16/int8: the worst-case element
                # product is 127*127 = 16129, and int16 overflows at 32767 in
                # just 2-3 dimensions; int8 would silently wrap. int8's value is
                # 4x smaller on-disk storage, not scan speed — the float32 path
                # below is the fast one (no copy/upcast).
                va = _np.frombuffer(a, dtype=_np.int8).astype(_np.int32)
                vb = _np.frombuffer(b, dtype=_np.int8).astype(_np.int32)
                return float(_np.dot(va, vb)) / (127.0 ** 2)
            va = array('b')
            va.frombytes(a)
            vb = array('b')
            vb.frombytes(b)
            # Both confirmed to be exactly `dim` bytes above, so strict is safe.
            return float(sum(x * y for x, y in zip(va, vb, strict=True))) / (127.0 ** 2)
        # float32 path (all dimensions are identical by the check above)
        if _np is not None:
            va = _np.frombuffer(a, dtype=_np.float32)
            vb = _np.frombuffer(b, dtype=_np.float32)
            return float(_np.dot(va, vb))
        va = array('f')
        va.frombytes(a)
        vb = array('f')
        vb.frombytes(b)
        # Both confirmed to be exactly `dim * 4` bytes above, so strict is safe.
        return float(sum(x * y for x, y in zip(va, vb, strict=True)))

# =============================================================================
# 3. PERSISTENT STORAGE & VAULT (SQLite + Filesystem)
# =============================================================================

# Ingest pipeline tuning (kept modest to stay lightweight)
DB_TIMEOUT           = 30.0
CONNECTION_POOL_SIZE = int(os.environ.get("HC_POOL_SIZE", "8"))
CHUNK_BATCH_SIZE     = 50
PIPELINE_QUEUE_SIZE  = 20
WORKER_THREADS       = int(os.environ.get("HC_WORKERS", "4"))

class ConnectionPool:
    """Reusable bounded pool of SQLite connections.

    Each connection is configured for WAL mode, a memory-mapped I/O window, an
    in-memory temp store, and a page cache for better concurrent read/write
    throughput than the previous open-a-new-connection-per-query behaviour.
    """

    def __init__(self, db_path: str, pool_size: int = CONNECTION_POOL_SIZE,
                 page_size: int = 16384):
        self.db_path = db_path
        self.pool_size = pool_size
        self.page_size = page_size
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        for _ in range(pool_size):
            self._pool.put(self._create_connection())

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=DB_TIMEOUT,
                               check_same_thread=False)
        conn.execute(f"PRAGMA page_size = {self.page_size}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA mmap_size = 536870912")    # 512 MB mmap window
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -64000")      # 64 MB page cache
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def get(self, timeout: float = 30.0) -> sqlite3.Connection:
        """Acquire a connection, creating an overflow one if the pool is busy."""
        try:
            conn = self._pool.get(timeout=timeout)
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                return self._create_connection()
        except queue.Empty:
            return self._create_connection()

    def put(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool, or close it if the pool is full."""
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            with suppress(Exception):
                conn.close()

    def close_all(self) -> None:
        """Close every connection currently sitting in the pool."""
        while not self._pool.empty():
            with suppress(Exception):
                self._pool.get_nowait().close()

class VaultManager:
    """Handles SQLite FTS indexing and filesystem storage."""

    def __init__(self, config: ConfigManager, vault_name: str | None = None):
        self.config = config
        self.vault_name = vault_name
        root_dir = config.get('storage.root_dir', 'hoardcore_data')
        if vault_name:
            # Guard against path traversal: a vault name must be a single
            # path-safe token (no separators, '..', or leading dots).
            safe = re.sub(r'[^A-Za-z0-9._-]+', '-', vault_name).strip('.-')
            if not safe or safe != vault_name:
                logger.warning(
                    f"Sanitized vault name {vault_name!r} -> {safe!r}"
                )
                vault_name = safe
            self.vault_name = vault_name
            root_dir = os.path.join(root_dir, vault_name)
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self.artifacts_dir = config.get('storage.artifacts_dir', 'artifacts')
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.db_path = os.path.join(self.root_dir, 'vault.db')
        self.embeddings = EmbeddingsEngine(config)
        self._vector_dim = self.embeddings.dim
        self.page_size = int(config.get('storage.page_size', 16384))
        self._pool = ConnectionPool(self.db_path, CONNECTION_POOL_SIZE, self.page_size)
        self._init_db()
        self.backfill_vectors()

    @contextmanager
    def _db(self) -> Iterator[tuple[sqlite3.Connection, sqlite3.Cursor]]:
        """Yield a committed-on-success, surfaced-on-exception DB cursor.

        Uses the connection pool (WAL + mmap + page cache). Guarantees the
        connection is always returned to the pool and transactions are never
        left dangling, even if a query raises mid-method.
        """
        conn = self._pool.get()
        try:
            cursor = conn.cursor()
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.put(conn)

    def _init_db(self) -> None:
        """Initialize SQLite with FTS5 virtual table."""
        with self._db() as (_conn, cursor):
            # Enable FTS5
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")

            # --- v0.6.0 schema migration ----------------------------------
            # Older vaults (pre-0.6.0) had documents without `version` /
            # `content_hash` and a UNIQUE(url) constraint. Rebuild the table in
            # place, preserving existing rows (each becomes version 1).
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
            )
            row = cursor.fetchone()
            if row and "version" not in (row[0] or ""):
                logger.info("Migrating documents table to v0.6.0 schema (WORM versions).")
                cursor.execute("ALTER TABLE documents RENAME TO documents_old")
                cursor.execute("""
                    CREATE TABLE documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT,
                        domain TEXT,
                        file_name TEXT,
                        content_type TEXT,
                        fetched_at REAL,
                        parser_used TEXT,
                        quality_score REAL,
                        total_chunks INTEGER,
                        metadata_json TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        content_hash TEXT,
                        UNIQUE(url, version)
                    )
                """)
                cursor.execute("""
                    INSERT INTO documents (
                        id, url, domain, file_name, content_type, fetched_at,
                        parser_used, quality_score, total_chunks, metadata_json,
                        version
                    )
                    SELECT id, url, domain, file_name, content_type, fetched_at,
                           parser_used, quality_score, total_chunks, metadata_json,
                           1
                    FROM documents_old
                """)
                cursor.execute("DROP TABLE documents_old")

            # Main table for metadata
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT,
                    domain TEXT,
                    file_name TEXT,
                    content_type TEXT,
                    fetched_at REAL,
                    parser_used TEXT,
                    quality_score REAL,
                    total_chunks INTEGER,
                    metadata_json TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    content_hash TEXT,
                    UNIQUE(url, version)
                )
            """)

            # Content-addressable chunk index for cross-document deduplication.
            # chunk_hash is the BLAKE2b-256 of the raw chunk text. The vector
            # table is keyed by this hash so identical chunks share one vector.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks_ca (
                    chunk_hash TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    url TEXT,
                    header_path TEXT,
                    metadata_json TEXT,
                    first_seen REAL
                )
            """)

            # FTS5 virtual table for full-text search
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    url,
                    header_path,
                    text,
                    metadata_json,
                    tokenize = 'porter unicode61'
                )
            """)

            # Trigger to clean up FTS when documents are updated
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_after_delete
                AFTER DELETE ON documents
                BEGIN
                    DELETE FROM chunks_fts WHERE url = OLD.url;
                END;
            """)

            # Vector index for hybrid retrieval. chunk_rowid mirrors the implicit
            # rowid of chunks_fts rows so vector and FTS results can be fused.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    chunk_rowid INTEGER PRIMARY KEY,
                    url TEXT,
                    vector BLOB
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunk_vec_url ON chunk_vectors(url)")

            # Recency weighting (P1.2) looks up documents by (url, fetched_at);
            # this index turns those searches from O(N) scans into index seeks.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_url_fetched "
                "ON documents(url, fetched_at DESC)"
            )

            # Content-addressable vector cache: identical chunks share one
            # embedding, so cross-document duplicate text is embedded once.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunk_vectors_ca (
                    chunk_hash TEXT PRIMARY KEY,
                    vector BLOB
                )
            """)

            # Near-duplicate index (optional, indexer.near_dedup): a 64-bit
            # simhash per stored chunk. Exact content handling stays in
            # chunks_ca; this catches near-identical re-crawled text whose
            # boilerplate differs. First-write wins, like chunks_ca.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks_simhash (
                    simhash INTEGER PRIMARY KEY,
                    chunk_hash TEXT,
                    url TEXT,
                    text TEXT,
                    first_seen REAL
                )
            """)

    def _get_domain_folder(self, url: str) -> str:
        parsed = urlparse(url)
        domain = parsed.netloc or "unknown_domain"
        domain = re.sub(r'[^\w\-\.]', '_', domain)
        folder = os.path.join(self.root_dir, domain)
        os.makedirs(folder, exist_ok=True)
        os.makedirs(os.path.join(folder, 'binaries'), exist_ok=True)
        os.makedirs(os.path.join(folder, 'extracted'), exist_ok=True)
        return folder

    def _generate_filename(self, url: str, content_type: str) -> str:
        """Generate a safe filename from URL."""
        parsed = urlparse(url)
        path = parsed.path or '/'
        name = os.path.basename(path) or 'index'
        # Remove query strings
        name = re.sub(r'\?.*$', '', name)
        # Sanitize characters that are illegal in filenames on some platforms
        # (Windows `:`/`*`, path separators, etc.) instead of hoping the URL
        # author cooperated (C6).
        name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('.-') or 'index'
        if not name:
            name = hashlib.blake2b(url.encode(), digest_size=8).hexdigest()[:16]

        # Determine extension
        ext_map = {
            'application/pdf': '.pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
            'application/epub+zip': '.epub',
            'text/html': '.html',
            'text/plain': '.txt'
        }
        ext = ext_map.get(content_type, '.bin')
        # Ensure unique by hashing. BLAKE2b-64 (16 hex chars) has a negligible
        # collision risk, vs the old 24-bit MD5 suffix which hit a 50% collision
        # probability around 4k URLs and silently overwrote files (C2).
        hash_suffix = hashlib.blake2b(url.encode(), digest_size=8).hexdigest()[:16]
        return f"{name}_{hash_suffix}{ext}"

    def save_binary(self, url: str, content_type: str, data: bytes) -> str:
        """Save binary file to domain/binaries/ folder."""
        folder = self._get_domain_folder(url)
        bin_dir = os.path.join(folder, 'binaries')
        filename = self._generate_filename(url, content_type)
        bin_path = os.path.join(bin_dir, filename)

        if not os.path.exists(bin_path):
            with open(bin_path, 'wb') as f:
                f.write(data)
            logger.info(f"Saved binary: {bin_path}")
        return bin_path

    def save_extracted_text(self, url: str, markdown: str, chunks: list[Chunk], meta: dict[str, Any]) -> None:
        """Save extracted text and chunks to domain/extracted/."""
        folder = self._get_domain_folder(url)
        ext_dir = os.path.join(folder, 'extracted')

        # Create a safe filename
        filename = self._generate_filename(url, meta.get('content_type', 'text/plain'))
        base_name = os.path.splitext(filename)[0]

        # Save full markdown
        md_path = os.path.join(ext_dir, f"{base_name}.content.md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown)

        # Save chunks JSON
        chunks_path = os.path.join(ext_dir, f"{base_name}.chunks.json")
        with open(chunks_path, 'w', encoding='utf-8') as f:
            json.dump([c.to_dict() for c in chunks], f, indent=2, ensure_ascii=False)

        logger.info(f"Saved extracted text to {ext_dir}")

    @staticmethod
    def _simhash(tokens: list[str]) -> int:
        """63-bit simhash over token features (FNV-1a feature hashing).

        Standard weighted simhash: each token's 64-bit hash votes up/down on
        every bit; the sign of each bit's total vote becomes the bit value.
        The top bit is forced clear so the value always fits SQLite's *signed*
        64-bit INTEGER — otherwise ~44% of documents fail ingest with
        "Python int too large to convert to SQLite INTEGER". All bits still
        vote; comparisons only ever see masked values, so hamming distances
        between them are unaffected.
        """
        v = [0] * 64
        for t in set(tokens):
            h = _fnv1a(t.encode("utf-8"))
            for i in range(64):
                v[i] += 1 if (h >> i) & 1 else -1
        out = 0
        for i in range(63):
            if v[i] > 0:
                out |= 1 << i
        return out

    def _filter_near_dupes(self, cursor: sqlite3.Cursor, url: str,
                           chunks: list[Chunk]) -> list[Chunk]:
        """Drop chunks near-duplicate (simhash hamming <= threshold) to already
        stored text, optionally, when config indexer.near_dedup is true.

        Exact-duplicate handling stays in chunks_ca; this catches near-identical
        re-crawled pages whose boilerplate differs. Kept chunks get their
        simhash recorded (first-write wins). Off by default because collapsing
        cross-source corroborating text would hide evidence — enable when
        crawling large sites and duplicate growth matters.
        """
        if not self.config.get('indexer.near_dedup', False):
            return chunks
        threshold = int(self.config.get('indexer.near_dedup_threshold', 3))
        existing = [row[0] for row in cursor.execute(
            "SELECT simhash FROM chunks_simhash").fetchall()]
        kept: list[Chunk] = []
        for chunk in chunks:
            tokens = EmbeddingsEngine._tokens(chunk.text)
            if not tokens:
                kept.append(chunk)  # no lexical features -> cannot judge
                continue
            sh = self._simhash(tokens)
            if any(hamming64(sh, e) <= threshold for e in existing):
                logger.debug(f"near-dup block (hamming<= {threshold}) for {url}")
                continue
            existing.append(sh)
            c_hash = hashlib.blake2b(chunk.text.encode("utf-8"),
                                     digest_size=32).hexdigest()
            cursor.execute(
                "INSERT OR IGNORE INTO chunks_simhash "
                "(simhash, chunk_hash, url, text, first_seen) VALUES (?, ?, ?, ?, ?)",
                (sh, c_hash, url, chunk.text, time.time()),
            )
            kept.append(chunk)
        if len(kept) != len(chunks):
            logger.info(f"near-dedup kept {len(kept)}/{len(chunks)} chunks for {url}")
        return kept

    def index_document(self, url: str, chunks: list[Chunk], meta: dict[str, Any]) -> None:
        """Insert/update document and chunks in SQLite FTS.

        WORM semantics: re-ingesting the same URL creates a new *version* row
        rather than overwriting the previous one, so the vault is append-only.
        Chunks are content-addressed (BLAKE2b-256); identical chunk text across
        documents shares a single canonical entry and is embedded only once.
        """
        if not self.config.get('indexer.enable_fts', True):
            return

        embed_ok = self.config.get('embeddings.enabled', True)
        domain = urlparse(url).netloc

        with self._db() as (_conn, cursor):
            # Optional near-duplicate filter (indexer.near_dedup) runs inside
            # the same transaction so skipped chunks never half-persist.
            chunks = self._filter_near_dupes(cursor, url, chunks)
            content_hash = hashlib.blake2b(
                "\n".join(c.text for c in chunks).encode("utf-8"),
                digest_size=32,
            ).hexdigest()

            # WORM: determine the next version for this URL (never overwrite).
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) FROM documents WHERE url = ?",
                (url,),
            )
            version = cursor.fetchone()[0] + 1

            # Insert document metadata (append-only; UNIQUE(url, version)).
            cursor.execute("""
                INSERT INTO documents (
                    url, domain, file_name, content_type, fetched_at,
                    parser_used, quality_score, total_chunks, metadata_json,
                    version, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url,
                domain,
                meta.get('file_name', ''),
                meta.get('content_type', ''),
                time.time(),
                meta.get('parser_used') or meta.get('parser', 'unknown'),
                meta.get('quality_score', 0.0),
                len(chunks),
                json.dumps(meta),
                version,
                content_hash,
            ))

            # Insert chunks into FTS + content-addressable dedup. The CA-vector
            # lookup runs on the SAME connection (no nested pool acquires).
            for chunk in chunks:
                text = chunk.text
                header = chunk.metadata.get('header_path', 'Root')
                c_hash = hashlib.blake2b(text.encode("utf-8"),
                                         digest_size=32).hexdigest()

                # Canonical dedup entry (INSERT OR IGNORE = first-write wins).
                cursor.execute("""
                    INSERT OR IGNORE INTO chunks_ca (
                        chunk_hash, text, url, header_path, metadata_json, first_seen
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (c_hash, text, url, header, json.dumps(chunk.metadata), time.time()))

                cursor.execute("""
                    INSERT INTO chunks_fts (url, header_path, text, metadata_json)
                    VALUES (?, ?, ?, ?)
                """, (url, header, text, json.dumps(chunk.metadata)))
                rowid = cursor.lastrowid

                if embed_ok:
                    # Dedup-aware embedding: reuse a cached vector for an
                    # identical chunk instead of recomputing it.
                    vec = self._embed_chunk(cursor, c_hash, text)
                    if vec is not None:
                        cursor.execute(
                            "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) "
                            "VALUES (?, ?, ?)",
                            (rowid, url, vec),
                        )

        logger.info(f"Indexed {len(chunks)} chunks for {url} (v{version})")

    def _embed_chunk(self, cursor: sqlite3.Cursor, c_hash: str, text: str) -> bytes | None:
        """Return a vector for *text*, reusing a cached one for an identical
        chunk hash. Runs on the caller's connection (no nested pool acquire).
        Returns None if embedding fails (non-fatal)."""
        try:
            cursor.execute(
                "SELECT vector FROM chunk_vectors_ca WHERE chunk_hash = ?",
                (c_hash,),
            )
            row = cursor.fetchone()
            if row is not None and row[0] is not None:
                return row[0]
        except Exception:
            pass
        try:
            vec = self.embeddings.vectorize(text)
        except Exception as e:  # embedding failures must not block indexing
            logger.warning(f"Embedding failed: {e}")
            return None
        if vec:
            with suppress(Exception):
                cursor.execute(
                    "INSERT OR IGNORE INTO chunk_vectors_ca (chunk_hash, vector) "
                    "VALUES (?, ?)",
                    (c_hash, vec),
                )
        return vec

    def backfill_vectors(self) -> int:
        """Compute and store embeddings for chunks missing one (or with a stale
        dimension). Returns count backfilled.

        If the configured embedding mode/dimension differs from what is stored
        (e.g. switching from the 256-dim sparse hash to a 384-dim dense model),
        the mismatched rows are recomputed in place. Rebuilds are resumable: an
        interrupted run simply leaves some rows stale, which the next run picks
        up, so no destructive DELETE-all is ever needed.
        """
        if not self.config.get('embeddings.enabled', True):
            return 0
        expected_bytes = self._vector_dim * self.embeddings.bytes_per_dim
        stale_dim = False
        with self._db() as (_conn, cursor):
            # Detect dimension mismatch across ALL stored vectors, not just a
            # LIMIT 1 sample: an interrupted earlier backfill can leave some
            # rows fresh and some stale, and a one-row probe would miss it.
            cursor.execute("SELECT COUNT(*) FROM chunk_vectors")
            vec_count = cursor.fetchone()[0]
            if vec_count > 0:
                cursor.execute(
                    "SELECT COUNT(*) FROM chunk_vectors WHERE length(vector) != ?",
                    (expected_bytes,),
                )
                stale_count = cursor.fetchone()[0]
                stale_dim = stale_count > 0
                if stale_dim:
                    logger.info(
                        f"{stale_count} of {vec_count} vectors have a stale "
                        f"dimension (expected {expected_bytes} bytes); "
                        f"recomputing them in place."
                    )

        # Cheap count check: fully vectorized AND matching dim -> 0 work.
        # Skip the count shortcut when dims are stale (rows exist but are wrong).
        if not stale_dim:
            with self._db() as (_conn, cursor):
                cursor.execute("SELECT COUNT(*) FROM chunks_fts")
                fts_count = cursor.fetchone()[0]
            if fts_count == vec_count:
                return 0

        # Select chunks that are missing a vector OR carry a wrong-dimension one.
        count = 0
        with self._db() as (_conn, cursor):
            cursor.execute(f"""
                SELECT c.rowid, c.url, c.text
                FROM chunks_fts c
                LEFT JOIN chunk_vectors v ON v.chunk_rowid = c.rowid
                WHERE v.chunk_rowid IS NULL
                   OR length(v.vector) != {expected_bytes}
            """)
            rows = cursor.fetchall()
            for rowid, url, text in rows:
                try:
                    vec = self.embeddings.vectorize(text)
                except Exception as e:
                    logger.warning(f"Backfill embedding failed {url}: {e}")
                    continue
                cursor.execute(
                    "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) VALUES (?, ?, ?)",
                    (rowid, url, vec)
                )
                count += 1
        if count:
            logger.info(f"Backfilled {count} chunk embeddings.")
        return count

    def verify_vault(self) -> bool:
        """Run a three-phase integrity check over the vault.

        Phase 1 — verify every document's chunk count and content hash.
        Phase 2 — verify content-addressable chunks are internally consistent.
        Phase 3 — verify every stored vector's dimension matches the engine.

        Returns True if no errors were found.
        """
        errors = 0
        checks = 0

        # Phase 1: for each URL, the total FTS chunk count must equal the sum of
        # the declared chunk counts across all of that URL's document versions
        # (WORM means one URL may span several version rows).
        with self._db() as (_conn, cursor):
            cursor.execute(
                "SELECT url, SUM(total_chunks) FROM documents GROUP BY url"
            )
            for url, declared_total in cursor.fetchall():
                cursor.execute(
                    "SELECT COUNT(*) FROM chunks_fts WHERE url = ?", (url,)
                )
                fts_count = cursor.fetchone()[0]
                checks += 1
                if fts_count != declared_total:
                    logger.error(
                        "MISMATCH: %s declares %d chunks (all versions) but FTS has %d",
                        url, declared_total, fts_count,
                    )
                    errors += 1

        # Phase 2: content-addressable chunks must have non-empty text.
        with self._db() as (_conn, cursor):
            cursor.execute("SELECT chunk_hash, text FROM chunks_ca")
            for c_hash, text in cursor.fetchall():
                checks += 1
                recomputed = hashlib.blake2b(
                    (text or "").encode("utf-8"), digest_size=32
                ).hexdigest()
                if recomputed != c_hash:
                    logger.error(
                        "CORRUPTION: chunk %s… text hash does not match",
                        c_hash[:16],
                    )
                    errors += 1

        # Phase 3: every vector must match the configured dimension.
        expected_bytes = self._vector_dim * self.embeddings.bytes_per_dim
        with self._db() as (_conn, cursor):
            cursor.execute("SELECT chunk_rowid, length(vector) FROM chunk_vectors")
            for rid, vlen in cursor.fetchall():
                checks += 1
                if vlen != expected_bytes:
                    logger.error(
                        "BAD DIM: vector for chunk %d is %d bytes (expected %d)",
                        rid, vlen, expected_bytes,
                    )
                    errors += 1

        if errors == 0:
            logger.info(f"Vault integrity PASS ({checks} checks).")
        else:
            logger.error(f"Vault integrity FAIL ({errors} error(s) across {checks} checks).")
        return errors == 0

    def migrate_page_size(self, target: int | None = None) -> bool:
        """Rewrite the vault DB at a different SQLite page size via `VACUUM INTO`.

        SQLite only honors `PRAGMA page_size` while a database file is still
        empty, so vaults created before the 16 KB default keep their old page
        size (typically 4096). This rebuilds the file at `target` bytes per
        page without touching live connections, preserving all data.

        Returns True if the vault was rewritten, False if it was already at the
        target size (or the rewrite failed).
        """
        target = int(target or self.page_size)
        with self._db() as (_conn, cursor):
            current = cursor.execute("PRAGMA page_size").fetchone()[0]
        if current == target:
            return False

        tmp_path = f"{self.db_path}.ps{target}"
        # SQLite's VACUUM INTO refuses to overwrite an existing file, so clear
        # any stale temp from a previous failed attempt before retrying.
        with suppress(FileNotFoundError):
            os.remove(tmp_path)
        ok = False
        try:
            # A dedicated connection outside the pool so WAL is quiet.
            conn = sqlite3.connect(self.db_path, timeout=DB_TIMEOUT)
            try:
                conn.execute(f"PRAGMA page_size = {target}")
                conn.execute(f"VACUUM INTO '{tmp_path}'")
            finally:
                conn.close()
            with sqlite3.connect(tmp_path) as v:
                new_size = v.execute("PRAGMA page_size").fetchone()[0]
            if new_size != target:
                logger.error(
                    f"migrate_page_size: VACUUM INTO produced {new_size}, "
                    f"expected {target}."
                )
                with suppress(FileNotFoundError):
                    os.remove(tmp_path)
                return False
            ok = True
        except Exception as e:
            logger.error(f"migrate_page_size failed: {e}")
            with suppress(FileNotFoundError):
                os.remove(tmp_path)
            return False

        # Swap the rebuilt file into place and refresh the pool.
        self._pool.close_all()
        os.replace(tmp_path, self.db_path)
        for suffix in ("-wal", "-shm"):
            with suppress(FileNotFoundError):
                os.remove(self.db_path + suffix)
        self._pool = ConnectionPool(self.db_path, CONNECTION_POOL_SIZE, self.page_size)
        logger.info(f"Vault page size migrated {current} -> {target} bytes.")
        return ok

    def ingest_chunks_parallel(self, url: str, chunks: list[Chunk],
                               meta: dict[str, Any]) -> None:
        """Ingest chunks through a parallel reader→embed→write pipeline.

        Kept optional (guarded by config 'indexer.parallel'): for typical
        research vaults the sequential path is fast enough, and this avoids
        thread overhead on small batches. When enabled, embedding work is
        spread across WORKER_THREADS threads while the DB writer stays single.
        """
        if not self.config.get('indexer.enable_fts', True):
            return
        if not self.config.get('indexer.parallel', False) or len(chunks) < 8:
            return self.index_document(url, chunks, meta)

        embed_ok = self.config.get('embeddings.enabled', True)
        work_q: queue.Queue = queue.Queue(maxsize=PIPELINE_QUEUE_SIZE)
        result_q: queue.Queue = queue.Queue(maxsize=PIPELINE_QUEUE_SIZE)
        error_holder: list[Exception] = []
        results: list[tuple[int, str, bytes | None]] = [None] * len(chunks)  # type: ignore[list-item]
        # A sentinel is pushed to the WORK queue (not result_q) and consumed by
        # the workers themselves, so shutdown can never race with the consumer
        # (A11). The reader then collects exactly len(chunks) results.
        sentinel = (-1, None)

        def _embed_worker() -> None:
            while True:
                idx, text = work_q.get()
                try:
                    if idx == -1:
                        return
                    vec = self.embeddings.vectorize(text) if embed_ok else None
                except Exception as e:
                    error_holder.append(e)
                    vec = None
                finally:
                    work_q.task_done()
                result_q.put((idx, vec))

        # Start the workers, then feed work and sentinels. Feeding after start
        # means the queue never fills up and block() can't deadlock even for
        # batches larger than PIPELINE_QUEUE_SIZE.
        threads = [threading.Thread(target=_embed_worker, daemon=True)
                   for _ in range(WORKER_THREADS)]
        for t in threads:
            t.start()
        for idx, chunk in enumerate(chunks):
            work_q.put((idx, chunk.text))
        # Wake EXACTLY the number of workers we started so none hang waiting.
        for _ in threads:
            work_q.put(sentinel)
        # Collect results CONCURRENTLY with the workers: result_q is bounded,
        # so draining it here (rather than after join) prevents workers from
        # blocking on a full queue while the main thread waits forever (A11).
        for _ in range(len(chunks)):
            idx, vec = result_q.get(timeout=10.0)
            results[idx] = vec  # type: ignore[assignment]
        # By now every worker has seen its sentinel (the only items left in the
        # queue) and exited, so join cannot hang.
        for t in threads:
            t.join()

        # Single writer thread commits the whole batch (dedup + vectors).
        with self._db() as (_conn, cursor):
            domain = urlparse(url).netloc
            chunks = self._filter_near_dupes(cursor, url, chunks)
            content_hash = hashlib.blake2b(
                "\n".join(c.text for c in chunks).encode("utf-8"),
                digest_size=32,
            ).hexdigest()
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) FROM documents WHERE url = ?",
                (url,),
            )
            version = cursor.fetchone()[0] + 1
            cursor.execute("""
                INSERT INTO documents (
                    url, domain, file_name, content_type, fetched_at,
                    parser_used, quality_score, total_chunks, metadata_json,
                    version, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url, domain, meta.get('file_name', ''),
                meta.get('content_type', ''), time.time(),
                meta.get('parser_used') or meta.get('parser', 'unknown'),
                meta.get('quality_score', 0.0), len(chunks),
                json.dumps(meta), version, content_hash,
            ))
            for idx, chunk in enumerate(chunks):
                text = chunk.text
                c_hash = hashlib.blake2b(text.encode("utf-8"),
                                         digest_size=32).hexdigest()
                cursor.execute("""
                    INSERT OR IGNORE INTO chunks_ca (
                        chunk_hash, text, url, header_path, metadata_json, first_seen
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (c_hash, text, url, chunk.metadata.get('header_path', 'Root'),
                      json.dumps(chunk.metadata), time.time()))
                cursor.execute("""
                    INSERT INTO chunks_fts (url, header_path, text, metadata_json)
                    VALUES (?, ?, ?, ?)
                """, (url, chunk.metadata.get('header_path', 'Root'), text,
                      json.dumps(chunk.metadata)))
                rowid = cursor.lastrowid
                vec = results[idx]
                if vec:
                    cursor.execute(
                        "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) "
                        "VALUES (?, ?, ?)",
                        (rowid, url, vec),
                    )
        if error_holder:
            logger.warning(f"{len(error_holder)} embedding errors during parallel ingest.")
        logger.info(f"Indexed {len(chunks)} chunks for {url} (v{version}, parallel)")

    @staticmethod
    def _fts_query(query: str) -> str | None:
        """Build a safe FTS5 MATCH expression from a raw user query.

        Wraps each whitespace token as a quoted phrase so FTS operators
        (quotes, parentheses, *, ^, :, -) in user input cannot alter the
        query semantics or raise syntax errors. Returns None if the query
        contains no usable tokens (e.g. empty, punctuation-only).
        """
        tokens = []
        for token in re.findall(r'\S+', query):
            cleaned = re.sub(r'["()*^:\-]', ' ', token)
            cleaned = ' '.join(cleaned.split())
            if cleaned:
                tokens.append(f'"{cleaned}"')
        return ' AND '.join(tokens) or None

    def search_vault(self, query: str, limit: int = 20, domain: str | None = None,
                     hybrid: bool | None = None) -> list[Chunk]:
        """Perform FTS5 search (or hybrid FTS+vector when enabled).

        Args:
            query: free-text query.
            limit: max results.
            domain: restrict to a netloc substring, if given.
            hybrid: None -> use config; True/False -> override.
        """
        if not self.config.get('indexer.enable_fts', True):
            return []

        if not query or not query.strip():
            return []  # empty/whitespace query -> no results, not a crash

        use_hybrid = self.config.get('embeddings.hybrid_search', True) if hybrid is None else hybrid
        if use_hybrid and self.embeddings.enabled:
            return self._search_hybrid(query, limit, domain)

        results = []
        with self._db() as (_conn, cursor):
            fts_match = self._fts_query(query)
            if not fts_match:
                return []  # punctuation-only query -> nothing to match
            where = "chunks_fts MATCH ?"
            params: list[Any] = [fts_match]

            if domain:
                where += " AND url LIKE ?"
                params.append(f'%{domain}%')

            # FTS5 query with ranking
            cursor.execute(f"""
                SELECT url, header_path, text, metadata_json, rank, rowid
                FROM chunks_fts
                WHERE {where}
                ORDER BY rank
                LIMIT ?
            """, (*params, limit))

            for row in cursor.fetchall():
                url, header_path, text, meta_json, rank, rowid = row
                meta = json.loads(meta_json)
                meta['search_rank'] = rank
                meta['source_url'] = url
                results.append(Chunk(text=text, metadata=meta))

        return results

    def _search_hybrid(self, query: str, limit: int, domain: str | None) -> list[Chunk]:
        """Fuse FTS5 keyword ranks and vector-similarity ranks via Reciprocal
        Rank Fusion (RRF). Returns Chunks best matching the query."""
        if not query or not query.strip():
            return []
        k = 60  # RRF constant
        fts_pool = int(self.config.get('indexer.search_limit', 20) * 3)
        vec_pool = self.config.get('embeddings.top_k', 40)

        with self._db() as (_conn, cursor):
            # --- FTS candidate list (rowid -> rank score) ---
            fts_match = self._fts_query(query)
            fts_where = "chunks_fts MATCH ?"
            fts_params: list[Any] = [fts_match]
            if domain:
                fts_where += " AND url LIKE ?"
                fts_params.append(f'%{domain}%')
            # A query with no FTS tokens (e.g. "*", operators only) still has
            # semantic content the embedding model can match: run the vector
            # scan alone instead of returning [] (A6).
            fts_rows: list[tuple[int, str]] = []
            if fts_match:
                cursor.execute(f"""
                    SELECT rowid, url FROM chunks_fts
                    WHERE {fts_where}
                    ORDER BY rank
                    LIMIT ?
                """, (*fts_params, fts_pool))
                fts_rows = cursor.fetchall()

            # --- FTS5 strong-signal fast path (P1.1) ---
            # When the FTS5 AND-match alone fills the requested result set, the
            # query is a strong keyword signal: skip the (more expensive) vector
            # scan entirely and return the FTS ranking directly. Tagged
            # retrieval='fts_fast' so downstream provenance is explicit.
            if (self.config.get('embeddings.fts_fast_path', True)
                    and len(fts_rows) >= limit):
                # Recency weighting must apply uniformly: without it, a 2-year-old
                # keyword match would rank identically to a fresh page even when
                # recency_half_life_days is configured (A3). We reorder the FTS
                # candidates by recency decay before slicing to the limit.
                half_life = float(self.config.get('embeddings.recency_half_life_days', 0) or 0)
                selected: list[tuple[int, str]] = fts_rows[:limit]
                if half_life > 0 and selected:
                    now = time.time()
                    urls = list({u for _rid, u in selected if u})
                    fetched_by_url: dict[str, float] = {}
                    if urls:
                        placeholders = ",".join("?" * len(urls))
                        cursor.execute(
                            f"SELECT url, MAX(fetched_at) FROM documents "
                            f"WHERE url IN ({placeholders}) GROUP BY url",
                            urls,
                        )
                        fetched_by_url = dict(cursor.fetchall())

                    def _decay(u: str) -> float:
                        fetched = fetched_by_url.get(u)
                        if not fetched:
                            return 1.0
                        age = max(0.0, (now - fetched) / 86400.0)
                        return 0.5 ** (age / half_life)

                    selected.sort(key=lambda t: (_decay(t[1]), t[0]), reverse=True)
                fast_ids = [rid for rid, _ in selected]
                if fast_ids:
                    placeholders = ",".join("?" * len(fast_ids))
                    order_map = {rid: i for i, rid in enumerate(fast_ids)}
                    cursor.execute(f"""
                        SELECT rowid, url, header_path, text, metadata_json
                        FROM chunks_fts WHERE rowid IN ({placeholders})
                    """, fast_ids)
                    fast_rows = cursor.fetchall()
                    fast_rows.sort(key=lambda r: order_map.get(r[0], 9999))
                    results: list[Chunk] = []
                    for _rid, url, _hp, text, meta_json in fast_rows:
                        meta = json.loads(meta_json)
                        meta['source_url'] = url
                        meta['retrieval'] = 'fts_fast'
                        meta['hybrid_score'] = None
                        # Confidence band is deliberately 'medium', not 'high':
                        # the vector scan was skipped, so semantic closeness is
                        # unverified. 'high' is reserved for hybrid hits that
                        # matched BOTH the keyword AND vector lists (see the
                        # confidence-band derivation below).
                        meta['confidence'] = 'medium'
                        results.append(Chunk(text=text, metadata=meta))
                    return results

            # --- vector candidate list (brute force; fine for a hoard vault) ---
            scored: list[tuple[float, int, str]] = []
            if vec_pool > 0:
                qvec = self.embeddings.vectorize(query)
                vec_where = ""
                vec_params: list[Any] = []
                if domain:
                    vec_where = " WHERE url LIKE ?"
                    vec_params.append(f'%{domain}%')
                cursor.execute(
                    "SELECT chunk_rowid, url, vector FROM chunk_vectors" + vec_where,
                    vec_params
                )
                for rid, u, blob in cursor.fetchall():
                    s = EmbeddingsEngine.cosine(qvec, blob, self._vector_dim)
                    scored.append((s, rid, u))
                scored.sort(key=lambda t: t[0], reverse=True)
                scored = scored[:vec_pool]

            # --- RRF fuse ---
            rrf: dict[int, float] = {}
            fts_rids: set[int] = set()
            for rank, (rid, _u) in enumerate(fts_rows):
                rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (k + rank + 1)
                fts_rids.add(rid)
            vec_rids: set[int] = set()
            for rank, (_score, rid, _u) in enumerate(scored):
                rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (k + rank + 1)
                vec_rids.add(rid)

            # --- Recency weighting (P1.2) ---
            # Optionally dampen stale hits: rrf *= 0.5 ** (age_days / half_life).
            # Half-life is 0 (disabled) by default.
            half_life = float(self.config.get('embeddings.recency_half_life_days', 0) or 0)
            if half_life > 0 and rrf:
                now = time.time()
                url_by_rid = dict(fts_rows)
                url_by_rid.update({rid: u for _s, rid, u in scored})
                urls = list({u for u in url_by_rid.values() if u})
                if urls:
                    placeholders = ",".join("?" * len(urls))
                    cursor.execute(
                        f"SELECT url, MAX(fetched_at) FROM documents "
                        f"WHERE url IN ({placeholders}) GROUP BY url",
                        urls,
                    )
                    fetched_by_url = dict(cursor.fetchall())
                    for rid, url in url_by_rid.items():
                        fetched = fetched_by_url.get(url)
                        if fetched:
                            age_days = max(0.0, (now - fetched) / 86400.0)
                            rrf[rid] *= 0.5 ** (age_days / half_life)

            if not rrf:
                return []

            fused = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:max(limit, 0)]
            order = fused[:limit] if limit > 0 else fused
            ids = [rid for rid, _ in (fused[:limit] if limit > 0 else fused)]

            # --- Confidence bands ---
            # Confidence is derived from how strong the fused evidence is, not
            # just ratio-to-top (which stays ~0.9 even for weak queries because
            # RRF scores cluster). Two signals:
            #   1. Whether the hit matched BOTH the FTS5 keyword list AND the
            #      vector list ("high" — terms present AND semantically close).
            #   2. The absolute top fused score: a strong result set tops out
            #      near 2/(k+1) ~= 0.032 (both lists agreed on #1), whereas a
            #      weak result set (vector-only, no keyword match) tops out near
            #      1/(k+1) ~= 0.016. Thresholds are set against this.
            conf_high_abs = float(self.config.get('embeddings.conf_high_abs', 0.025))
            conf_low_abs = float(self.config.get('embeddings.conf_low_abs', 0.020))
            conf_by_rid: dict[int, str] = {}
            for rid, score in order:
                matched_both = rid in fts_rids and rid in vec_rids
                if matched_both or score >= conf_high_abs:
                    conf_by_rid[rid] = "high"
                elif score >= conf_low_abs:
                    conf_by_rid[rid] = "medium"
                else:
                    conf_by_rid[rid] = "low"

            results: list[Chunk] = []
            if ids:
                placeholders = ",".join("?" * len(ids))
                order_map = {rid: i for i, (rid, _) in enumerate(order)}
                cursor.execute(f"""
                    SELECT rowid, url, header_path, text, metadata_json
                    FROM chunks_fts WHERE rowid IN ({placeholders})
                """, ids)
                rows = cursor.fetchall()
                rows.sort(key=lambda r: order_map.get(r[0], 9999))
                for rid, url, _hp, text, meta_json in rows:
                    meta = json.loads(meta_json)
                    meta['hybrid_score'] = rrf.get(rid, 0.0)
                    meta['confidence'] = conf_by_rid.get(rid, "low")
                    meta['source_url'] = url
                    meta['retrieval'] = 'hybrid'
                    results.append(Chunk(text=text, metadata=meta))
            return results

    def document_exists(self, url: str, ttl_seconds: int) -> bool:
        """Check if a document is in the vault and not expired."""
        with self._db() as (_conn, cursor):
            cursor.execute(
                "SELECT fetched_at FROM documents WHERE url = ?",
                (url,)
            )
            row = cursor.fetchone()
        if not row:
            return False
        fetched_at = row[0]
        return (time.time() - fetched_at) < ttl_seconds

    def get_chunks_for_url(self, url: str) -> list[Chunk]:
        """Return every stored chunk for a URL (in insertion order)."""
        results: list[Chunk] = []
        with self._db() as (_conn, cursor):
            cursor.execute(
                "SELECT header_path, text, metadata_json FROM chunks_fts WHERE url = ?",
                (url,)
            )
            for header_path, text, meta_json in cursor.fetchall():
                meta = json.loads(meta_json)
                if "source_url" not in meta:
                    meta["source_url"] = url
                if header_path and "header_path" not in meta:
                    meta["header_path"] = header_path
                results.append(Chunk(text=text, metadata=meta))
        return results

# =============================================================================
# 4. NETWORK RESILIENCE CORE
# =============================================================================

class NetworkFetcher:
    """Executes explicit strategy chain with pre-flight check."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self._cookie_string = config.get('auth.cookie_string', '')
        self._solver_enabled = config.get('solver.enabled', False)
        self._solver_url = config.get('solver.url', 'http://localhost:8191/v1')
        self._solver_timeout = config.get('solver.solver_timeout', 60)
        self._user_agent = config.get('general.user_agent', 'HoardCore/5.0')
        self._timeout = config.get('general.timeout_seconds', 30)
        self._max_retries = config.get('general.max_retries', 2)
        self._enable_preflight = config.get('network.enable_preflight', True)

    def _parse_cookies(self) -> dict[str, str]:
        cookies = {}
        if not self._cookie_string:
            return cookies
        for part in self._cookie_string.split(';'):
            part = part.strip()
            if '=' in part:
                key, val = part.split('=', 1)
                cookies[key.strip()] = val.strip()
        return cookies

    async def preflight(self, url: str) -> bool:
        if not self._enable_preflight or not self._parse_cookies():
            return True

        try:
            async with aiohttp.ClientSession() as session, session.head(
                url,
                cookies=self._parse_cookies(),
                headers={'User-Agent': self._user_agent},
                timeout=ClientTimeout(total=5),
                allow_redirects=False
            ) as resp:
                blocked = resp.status in (403, 429)
                captcha_redirect = (
                    resp.status in (302, 303)
                    and 'captcha' in resp.headers.get('Location', '').lower()
                )
                return not (blocked or captcha_redirect)
        except Exception as e:
            # Fail CLOSED: if the preflight itself errors (DNS, TLS, transient
            # 5xx), the right default is to abort the fetch, not to proceed.
            # Proceeding can hit Cloudflare with a request the probe could
            # already have rejected, and failing open makes a soft-left case
            # on any server deployment (C1).
            logger.warning(f"Preflight check failed for {url}: {e}")
            return False

    async def _fetch_aiohttp(self, url: str) -> tuple[str | None, bytes | None, str]:
        """Attempt 1: Standard aiohttp. Returns (text, binary, content_type)."""
        cookies = self._parse_cookies()
        headers = {'User-Agent': self._user_agent}
        connector = TCPConnector(force_close=True, enable_cleanup_closed=True, ttl_dns_cache=300)
        timeout = ClientTimeout(total=self._timeout)

        try:
            async with aiohttp.ClientSession(connector=connector, headers=headers) as session, session.get(
                url, cookies=cookies, timeout=timeout, allow_redirects=True
            ) as resp:
                content_type = resp.headers.get('Content-Type', 'text/plain').split(';')[0].strip()
                if resp.status == 200:
                    if 'text' in content_type:
                        return await resp.text(), None, content_type
                    else:
                        return None, await resp.read(), content_type
                elif resp.status == 403:
                    logger.warning("aiohttp: 403 Blocked.")
                    return None, None, content_type
                else:
                    logger.warning(f"aiohttp: Status {resp.status}")
                    return None, None, content_type
        except Exception as e:
            logger.debug(f"aiohttp failed: {e}")
            return None, None, ''

    async def _fetch_curl_cffi(self, url: str) -> tuple[str | None, bytes | None, str]:
        if not CURL_AVAILABLE:
            return None, None, ''

        cookies = self._parse_cookies()
        try:
            def _sync_fetch():
                resp = curl_requests.get(
                    url,
                    cookies=cookies,
                    headers={'User-Agent': self._user_agent},
                    impersonate="chrome120",
                    timeout=self._timeout,
                )
                return resp
            resp = await asyncio.to_thread(_sync_fetch)

            content_type = resp.headers.get('Content-Type', 'text/plain').split(';')[0].strip()
            if resp.status_code == 200:
                if 'text' in content_type:
                    return resp.text, None, content_type
                else:
                    return None, resp.content, content_type
            elif resp.status_code == 403:
                logger.warning("curl_cffi: 403 Blocked.")
                return None, None, content_type
            else:
                logger.warning(f"curl_cffi: Status {resp.status_code}")
                return None, None, content_type
        except Exception as e:
            logger.debug(f"curl_cffi failed: {e}")
            return None, None, ''

    async def _fetch_flaresolverr(self, url: str) -> tuple[str | None, bytes | None, str]:
        if not self._solver_enabled:
            return None, None, ''

        logger.info("FlareSolverr: Solving challenge...")
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self._solver_timeout * 1000,
            "userAgent": self._user_agent,
        }
        if self._cookie_string:
            payload["cookies"] = self._parse_cookies()

        try:
            timeout = ClientTimeout(total=self._solver_timeout + 10)
            async with aiohttp.ClientSession() as session, session.post(
                self._solver_url, json=payload, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    return None, None, ''
                data = await resp.json()
                if data.get("status") != "ok":
                    return None, None, ''
                solution = data.get("solution", {})
                if solution.get("status") == 200:
                    content_type = solution.get('headers', {}).get('Content-Type', 'text/html').split(';')[0]
                    response = solution.get('response', '')
                    if 'text' in content_type:
                        return response, None, content_type
                    else:
                        # FlareSolverr usually returns binary as b64, but we handle text mostly
                        return None, response.encode('utf-8'), content_type
                return None, None, ''
        except Exception as e:
            logger.error(f"FlareSolverr failed: {e}")
            return None, None, ''

    async def fetch(self, url: str, strategy: str) -> tuple[str | None, bytes | None, str]:
        """
        Execute the explicit strategy chain.
        Returns: (text, binary_data, content_type)
        """
        logger.info(f"Fetching {url} with strategy: {strategy}")

        # Preflight validation
        if (self._enable_preflight and self._parse_cookies()
                and not await self.preflight(url)):
            raise RuntimeError("CF_COOKIE_EXPIRED")

        # Strategy dispatch
        text, binary, ctype = None, None, ''

        if strategy == "fast":
            text, binary, ctype = await self._fetch_aiohttp(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            raise RuntimeError("FETCH_FAILED")

        elif strategy == "balanced":
            text, binary, ctype = await self._fetch_aiohttp(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            text, binary, ctype = await self._fetch_curl_cffi(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            raise RuntimeError("FETCH_FAILED")

        elif strategy == "aggressive":
            text, binary, ctype = await self._fetch_aiohttp(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            text, binary, ctype = await self._fetch_curl_cffi(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            text, binary, ctype = await self._fetch_flaresolverr(url)
            if text is not None or binary is not None:
                return text, binary, ctype
            raise RuntimeError("FETCH_FAILED")

        raise RuntimeError("FETCH_FAILED")

# =============================================================================
# 5. DOCUMENT PARSERS (Universal)
# =============================================================================

class DocumentParser:
    """Parses HTML, PDF, DOCX, EPUB into markdown text."""

    # Lazy/optional binary parsers. Imported on first use so that HTML-only
    # scraping works without the heavy PDF/DOCX/EPUB libraries installed.
    _fitz = None
    _docx = None
    _epub = None
    _ocr_engine = None
    _ocr_engine_ready = False

    @classmethod
    def _import_binary_parsers(cls) -> None:
        global FITZ_AVAILABLE, DOCX_AVAILABLE, EPUB_AVAILABLE, RAPIDOCR_AVAILABLE, _BINARY_IMPORTED
        if _BINARY_IMPORTED:
            return
        _BINARY_IMPORTED = True
        try:
            import fitz  # PyMuPDF for PDFs
            cls._fitz = fitz
            FITZ_AVAILABLE = True
        except ImportError:
            FITZ_AVAILABLE = False
            print("Warning: PyMuPDF (fitz) not installed. PDF parsing disabled.", file=sys.stderr)
        try:
            import docx  # python-docx
            cls._docx = docx
            DOCX_AVAILABLE = True
        except ImportError:
            DOCX_AVAILABLE = False
            print("Warning: python-docx not installed. DOCX parsing disabled.", file=sys.stderr)
        try:
            from ebooklib import epub  # ebooklib
            cls._epub = epub
            EPUB_AVAILABLE = True
        except ImportError:
            EPUB_AVAILABLE = False
            print("Warning: ebooklib not installed. EPUB parsing disabled.", file=sys.stderr)
        try:
            from rapidocr_onnxruntime import RapidOCR  # optional PDF OCR fallback
            cls._RapidOCR = RapidOCR
            RAPIDOCR_AVAILABLE = True
        except ImportError:
            RAPIDOCR_AVAILABLE = False
            print("Warning: rapidocr_onnxruntime not installed. PDF OCR fallback disabled.", file=sys.stderr)

    @classmethod
    def _get_ocr_engine(cls):
        """Return the shared RapidOCR engine (one instance, lazy) or None."""
        if not RAPIDOCR_AVAILABLE:
            return None
        if not cls._ocr_engine_ready:
            try:
                cls._ocr_engine = cls._RapidOCR()
            except Exception as e:
                logger.warning(f"Failed to initialise RapidOCR engine: {e}")
                cls._ocr_engine = None
            finally:
                cls._ocr_engine_ready = True
        return cls._ocr_engine

    @staticmethod
    def _ocr_page(page, dpi: int = 200) -> str:
        """OCR a single rendered page; returns extracted lines or '' if unavailable."""
        engine = DocumentParser._get_ocr_engine()
        if engine is None:
            return ""
        try:
            pix = page.get_pixmap(dpi=dpi)
            result = engine(pix.tobytes("png"))
            items = result[0] if isinstance(result, (list, tuple)) else result
            if not items:
                return ""
            lines = []
            for item in items:
                try:
                    box, text = item[0], item[1]
                    top = min(pt[1] for pt in box)
                    left = min(pt[0] for pt in box)
                except (TypeError, ValueError, IndexError):
                    top, left, text = 0, 0, ""
                text = str(text).strip()
                if text:
                    lines.append((top, left, text))
            lines.sort(key=lambda t: (int(t[0]) // 4, t[1]))
            return "\n".join(t[2] for t in lines)
        except Exception as e:
            logger.warning(f"OCR failed on a page: {e}")
            return ""

    @staticmethod
    async def parse_pdf(binary: bytes) -> tuple[str, dict[str, Any]]:
        """Extract text from PDF using PyMuPDF."""
        DocumentParser._import_binary_parsers()
        if not FITZ_AVAILABLE:
            return "", {"parser": "failed", "error": "PyMuPDF not installed"}
        try:
            doc = DocumentParser._fitz.open(stream=binary, filetype="pdf")
            text_parts = []
            ocr_pages = 0
            meta = {"page_count": doc.page_count, "parser": "pymupdf"}

            for page_num in range(doc.page_count):
                page = doc.load_page(page_num)
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"## Page {page_num + 1}\n\n{text.strip()}")
                    continue
                # Scanned / image-only page: fall back to OCR when available.
                ocr_text = DocumentParser._ocr_page(page)
                if ocr_text:
                    text_parts.append(f"## Page {page_num + 1} (ocr)\n\n{ocr_text}")
                    ocr_pages += 1
                else:
                    text_parts.append(f"## Page {page_num + 1} (ocr: no text extracted)\n\n")

            doc.close()
            if ocr_pages:
                meta["parser"] = "pymupdf+ocr"
                meta["ocr_pages"] = ocr_pages
            full_text = "\n\n".join(text_parts)
            return full_text, meta
        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
            return "", {"parser": "failed", "error": str(e)}

    @staticmethod
    async def parse_docx(binary: bytes) -> tuple[str, dict[str, Any]]:
        """Extract text from DOCX."""
        DocumentParser._import_binary_parsers()
        if not DOCX_AVAILABLE:
            return "", {"parser": "failed", "error": "python-docx not installed"}
        try:
            doc = DocumentParser._docx.Document(io.BytesIO(binary))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            full_text = "\n\n".join(paragraphs)
            return full_text, {"parser": "python-docx", "paragraph_count": len(paragraphs)}
        except Exception as e:
            logger.error(f"DOCX parsing failed: {e}")
            return "", {"parser": "failed", "error": str(e)}

    @staticmethod
    async def parse_epub(binary: bytes) -> tuple[str, dict[str, Any]]:
        """Extract text from EPUB."""
        DocumentParser._import_binary_parsers()
        if EPUB_AVAILABLE:
            try:
                # Use ebooklib to parse
                book = DocumentParser._epub.read_epub(io.BytesIO(binary))
                text_parts = []
                for item in book.get_items():
                    if item.get_type() == 9:  # ITEM_DOCUMENT
                        # Extract text from XHTML using regex
                        content = item.get_content().decode('utf-8', errors='ignore')
                        # Simple tag stripping
                        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
                        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                        content = re.sub(r'<[^>]+>', ' ', content)
                        content = re.sub(r'\s+', ' ', content).strip()
                        if content:
                            text_parts.append(content)
                full_text = "\n\n".join(text_parts)
                return full_text, {"parser": "ebooklib", "section_count": len(text_parts)}
            except Exception as e:
                logger.error(f"EPUB parsing failed: {e}")
                # Fall through to zipfile fallback
        else:
            logger.error("EPUB parsing skipped: ebooklib not installed.")
        # Fallback to zipfile (works without ebooklib)
        try:
            with zipfile.ZipFile(io.BytesIO(binary)) as zf:
                text_parts = []
                for name in zf.namelist():
                    if name.endswith('.xhtml') or name.endswith('.html'):
                        content = zf.read(name).decode('utf-8', errors='ignore')
                        content = re.sub(r'<[^>]+>', ' ', content)
                        content = re.sub(r'\s+', ' ', content).strip()
                        if content:
                            text_parts.append(content)
                full_text = "\n\n".join(text_parts)
                return full_text, {"parser": "zipfile_fallback", "section_count": len(text_parts)}
        except Exception as e2:
            logger.error(f"EPUB fallback failed: {e2}")
            return "", {"parser": "failed", "error": str(e2)}

    @staticmethod
    async def clean_html(html: str, url: str) -> tuple[str, dict[str, Any]]:
        """Clean HTML using trafilatura + readability fallback."""
        results = {}

        def _extract_trafilatura():
            return trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                output_format="markdown"
            ) or ""

        def _extract_readability():
            doc = Document(html)
            return doc.summary()

        traf_task = asyncio.to_thread(_extract_trafilatura)
        read_task = asyncio.to_thread(_extract_readability)

        try:
            traf_md, read_html = await asyncio.gather(traf_task, read_task)
            results["trafilatura"] = traf_md

            if read_html:
                # Rough conversion to markdown-ish text
                clean = re.sub(r'<script[^>]*>.*?</script>', '', read_html, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
                for tag in ['div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr']:
                    clean = re.sub(f'</{tag}>', '\n', clean, flags=re.IGNORECASE)
                clean = re.sub(r'<br\s*/?>', '\n', clean, flags=re.IGNORECASE)
                clean = re.sub(r'<[^>]+>', '', clean)
                import html as html_parser
                clean = html_parser.unescape(clean)
                lines = [line.strip() for line in clean.split('\n') if line.strip()]
                results["readability"] = '\n\n'.join(lines)
            else:
                results["readability"] = ""
        except Exception as e:
            logger.warning(f"HTML extraction failed: {e}")
            results = {"trafilatura": "", "readability": ""}

        # Choose best
        if len(results.get("trafilatura", "")) > 100:
            return results["trafilatura"], {"parser": "trafilatura"}
        elif len(results.get("readability", "")) > 100:
            return results["readability"], {"parser": "readability"}
        else:
            # Fallback: strip everything
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
            body = body_match.group(1) if body_match else html
            body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r'<[^>]+>', ' ', body)
            body = re.sub(r'\s+', ' ', body).strip()
            return body, {"parser": "fallback"}

    @staticmethod
    async def parse_binary(content_type: str, binary: bytes) -> tuple[str, dict[str, Any]]:
        """Route binary to appropriate parser based on content type."""
        parsers = {
            'application/pdf': DocumentParser.parse_pdf,
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DocumentParser.parse_docx,
            'application/epub+zip': DocumentParser.parse_epub,
        }

        parser = parsers.get(content_type)
        if parser:
            return await parser(binary)
        else:
            # Unknown binary, try to decode as text
            try:
                text = binary.decode('utf-8', errors='ignore')
                return text, {"parser": "binary_as_text"}
            except Exception:
                return "", {"parser": "unknown_binary", "error": "Cannot parse"}

# =============================================================================
# 6. SEMANTIC CHUNKER
# =============================================================================

class SemanticChunker:
    """Splits text into semantic chunks respecting headers."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.max_tokens = config.get('chunking.max_tokens', 512)
        self.strategy = config.get('chunking.strategy', 'heading')

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 4

    async def chunk(self, markdown: str, url: str, parser_meta: dict[str, Any]) -> list[Chunk]:
        if not markdown:
            return [Chunk(text="[Empty content]", metadata={"source": url, "empty": True})]

        # If source is a binary (PDF/DOCX), use paragraph strategy
        if parser_meta.get('parser') in ['pymupdf', 'pymupdf+ocr', 'python-docx', 'ebooklib']:
            strategy = "paragraph"
        else:
            strategy = self.strategy

        lines = markdown.split('\n')
        chunks = []
        current_chunk_lines = []
        header_stack = []  # (depth, title)

        def get_header_path() -> str:
            return " > ".join([h[1] for h in header_stack])

        def flush_chunk():
            nonlocal current_chunk_lines
            if not current_chunk_lines:
                return
            text = '\n'.join(current_chunk_lines).strip()
            if text:
                chunks.append(Chunk(
                    text=text,
                    metadata={
                        "source": url,
                        "header_path": get_header_path(),
                        "parser": parser_meta.get('parser', 'unknown')
                    }
                ))
            current_chunk_lines = []

        for line in lines:
            stripped = line.strip()
            header_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)

            if header_match and strategy == "heading":
                depth = len(header_match.group(1))
                title = header_match.group(2).strip()

                flush_chunk()

                while header_stack and header_stack[-1][0] >= depth:
                    header_stack.pop()
                header_stack.append((depth, title))
                current_chunk_lines.append(line)
                continue

            current_chunk_lines.append(line)

            if self._estimate_tokens('\n'.join(current_chunk_lines)) > self.max_tokens:
                flush_chunk()

        flush_chunk()

        if not chunks:
            chunks.append(Chunk(
                text=markdown.strip(),
                metadata={"source": url, "header_path": "Root", "parser": parser_meta.get('parser', 'unknown')}
            ))

        return chunks

# =============================================================================
# 7. CRAWLER (Sitemap & Robots)
# =============================================================================

class CrawlerPlanner:
    """Parses robots.txt and sitemaps to discover URLs."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.respect_robots = config.get('crawler.respect_robots', True)
        self.sitemap_limit = config.get('crawler.sitemap_limit', 500)

    async def get_robots_urls(self, domain: str) -> list[str]:
        """Fetch robots.txt and extract sitemap URLs."""
        if not self.respect_robots:
            return []

        base_url = f"{domain}/robots.txt"
        try:
            async with aiohttp.ClientSession() as session, session.get(
                base_url, timeout=10
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    sitemap_urls = re.findall(r'^Sitemap:\s*(.+)$', text, re.MULTILINE | re.IGNORECASE)
                    return [url.strip() for url in sitemap_urls]
        except Exception as e:
            logger.warning(f"Failed to fetch robots.txt: {e}")

        # Fallback to default sitemap location
        return [f"{domain}/sitemap.xml"]

    @staticmethod
    def _extract_locs(xml: str) -> list[str]:
        """Extract <loc> URLs from sitemap XML.

        Namespace-aware via lxml (a guaranteed dependency); falls back to a
        regex scan if the payload cannot be parsed as XML.
        """
        try:
            from lxml import etree as _etree
            root = _etree.fromstring(xml.encode("utf-8"))
            locs: list[str] = []
            for el in root.iter():
                if el.text and el.text.strip() and _etree.QName(el).localname == "loc":
                    locs.append(el.text.strip())
        except Exception:
            locs = [
                loc.strip()
                for loc in re.findall(
                    r"<loc[^>]*>\s*(.*?)\s*</loc>", xml, re.IGNORECASE | re.DOTALL
                )
            ]
        return locs

    async def parse_sitemap(self, sitemap_url: str) -> list[str]:
        """Parse sitemap XML and extract URLs."""
        try:
            async with aiohttp.ClientSession() as session, session.get(
                sitemap_url, timeout=30
            ) as resp:
                if resp.status != 200:
                    return []
                xml = await resp.text()
            return list(dict.fromkeys(self._extract_locs(xml)))[:self.sitemap_limit]
        except Exception as e:
            logger.warning(f"Failed to parse sitemap {sitemap_url}: {e}")
            return []

    async def discover_urls(self, url: str) -> list[str]:
        """Discover URLs for a given domain."""
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        sitemap_urls = await self.get_robots_urls(domain)
        all_urls = []
        for sitemap_url in sitemap_urls:
            urls = await self.parse_sitemap(sitemap_url)
            all_urls.extend(urls)

        # Deduplicate
        return list(dict.fromkeys(all_urls))

# =============================================================================
# 8. WEB DISCOVERY (feed the crawler from a live search query)
# =============================================================================

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class WebSearchProvider:
    """Discovers URLs from a live web query.

    Tries providers in order (DuckDuckGo HTML -> Mojeek HTML), each driven
    through the SAME resilient fetch chain as the crawler (aiohttp ->
    curl_cffi -> FlareSolverr), with bounded retry + exponential backoff on
    transient failures. Returns candidate SearchResults for the orchestrator
    to feed into _ingest_many.
    """

    def __init__(self, config: ConfigManager, fetcher: NetworkFetcher):
        self.config = config
        self.fetcher = fetcher
        self.max_retries = int(config.get('discovery.max_retries', 2))
        self.backoff_base = float(config.get('discovery.backoff_seconds', 1.5))

    @staticmethod
    def _clean_title(raw: str) -> str:
        return re.sub(r"<[^>]+>", "", raw).strip()

    async def _fetch_with_backoff(self, url: str, strategy: str) -> str | None:
        """Fetch a search page, retrying transient failures with backoff."""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                text, _binary, _ctype = await self.fetcher.fetch(url, strategy)
                if text:
                    return text
                raise RuntimeError("empty search response")
            except Exception as e:  # transient: rate-limit, 5xx, timeout
                last_exc = e
                if attempt < self.max_retries:
                    delay = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        f"Discovery attempt {attempt + 1}/{self.max_retries + 1} "
                        f"failed ({e}); retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
        logger.error(f"Discovery search failed after retries: {last_exc}")
        return None

    @staticmethod
    def _parse_duckduckgo(text: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            text, re.IGNORECASE | re.DOTALL
        ):
            href, title_raw = m.group(1), m.group(2)
            target = href
            if "uddg=" in href:
                target = unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
            if not target.startswith("http"):
                continue
            results.append(SearchResult(
                title=WebSearchProvider._clean_title(title_raw),
                url=target
            ))
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _parse_mojeek(text: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for m in re.finditer(
            r'<a class="ob"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            text, re.IGNORECASE | re.DOTALL
        ):
            href, title_raw = m.group(1), m.group(2)
            if not href.startswith("http"):
                continue
            results.append(SearchResult(
                title=WebSearchProvider._clean_title(title_raw),
                url=href
            ))
            if len(results) >= max_results:
                break
        return results

    async def _try_provider(self, url: str, strategy: str, max_results: int,
                            parser) -> list[SearchResult]:
        text = await self._fetch_with_backoff(url, strategy)
        if not text:
            return []
        return parser(text, max_results)

    async def search(self, query: str, max_results: int = 10,
                     strategy: str = "aggressive") -> list[SearchResult]:
        q = re.sub(r"\s+", "+", query.strip())
        # (label, url, parser) ordered by preference; later entries are fallbacks.
        providers = [
            ("duckduckgo", f"https://html.duckduckgo.com/html/?q={q}",
             self._parse_duckduckgo),
            ("mojeek", f"https://www.mojeek.com/search?q={q}",
             self._parse_mojeek),
        ]

        last_results: list[SearchResult] = []
        for label, url, parser in providers:
            results = await self._try_provider(url, strategy, max_results, parser)
            if results:
                logger.info(f"Discovery provider '{label}' returned {len(results)} results.")
                return results
            logger.warning(f"Discovery provider '{label}' returned nothing; trying fallback.")
            last_results = results

        return last_results


# =============================================================================
# 9. MAIN ORCHESTRATOR
# =============================================================================

class HoardCore:
    """Main entry point for scraping, crawling, and searching."""

    def __init__(self, vault_name: str | None = None):
        self.config = ConfigManager()
        self.vault = VaultManager(self.config, vault_name)
        self.vault_name = self.vault.vault_name
        self.fetcher = NetworkFetcher(self.config)
        self.parser = DocumentParser()
        self.chunker = SemanticChunker(self.config)
        self.crawler = CrawlerPlanner(self.config)
        self.discovery = WebSearchProvider(self.config, self.fetcher)
        self.save_binary = self.config.get('storage.save_binary', True)
        self.save_raw_html = self.config.get('storage.save_raw_html', False)

    @property
    def artifacts_dir(self) -> str:
        """Directory for finished research deliverables (reports, syntheses, audits)."""
        return self.vault.artifacts_dir

    def _artifact_day_subdir(self) -> str:
        """Day-scoped artifacts subdirectory, e.g. artifacts/2026-08-10/.

        Used when storage.artifacts_by_day is enabled so finished deliverables
        are grouped by the day they were written instead of piling up flat in
        the artifacts root.
        """
        day = time.strftime("%Y-%m-%d")
        path = os.path.join(self.artifacts_dir, day)
        os.makedirs(path, exist_ok=True)
        return path

    def resolve_artifact_out(self, out_path: str | None) -> str:
        """Map an artifact target to its on-disk path honoring artifacts_by_day.

        When storage.artifacts_by_day is true, any path that targets the
        artifacts directory is re-scoped into the current-day subfolder; paths
        elsewhere (e.g. /tmp/scratch.md) are left untouched so callers keep
        full control of out-of-vault writes.
        """
        if not self.config.get('storage.artifacts_by_day', True):
            return out_path
        if out_path is None:
            return os.path.join(self._artifact_day_subdir(), "grounding_context.md")
        artifacts_root = os.path.abspath(self.artifacts_dir) + os.sep
        if os.path.abspath(out_path).startswith(artifacts_root):
            return os.path.join(self._artifact_day_subdir(), os.path.basename(out_path))
        return out_path

    def write_artifact(self, filename: str, content: str) -> str:
        """Write a research deliverable into the artifacts directory.

        Returns the absolute path written. Raises if the filename would escape
        the artifacts directory. Honors storage.artifacts_by_day: files land in
        an artifacts/YYYY-MM-DD/ subfolder.
        """
        if os.path.basename(filename) != filename:
            raise ValueError(f"artifact filename must be a bare name, got {filename!r}")
        target = self.resolve_artifact_out(os.path.join(self.artifacts_dir, filename))
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Artifact written -> {target}")
        return target

    def organize_artifacts_by_day(self) -> list[str]:
        """Move flat artifacts/ files into per-day subfolders by mtime.

        One-time housekeeping for the artifacts-by-day feature: any deliverable
        living directly in the artifacts root is re-homed to
        artifacts/YYYY-MM-DD/ based on its file mtime. Only files (not
        directories) at the artifacts root are touched, and any day-subfolder
        names already present are skipped. Returns the list of new paths.

        Run when storage.artifacts_by_day is true; no-op otherwise.
        """
        if not self.config.get('storage.artifacts_by_day', True):
            return []
        root = self.artifacts_dir
        migrated: list[str] = []
        for name in sorted(os.listdir(root)):
            src = os.path.join(root, name)
            if not os.path.isfile(src):
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(src)))
            dest_dir = os.path.join(root, day)
            dest = os.path.join(dest_dir, name)
            if os.path.abspath(dest) == os.path.abspath(src):
                continue
            os.makedirs(dest_dir, exist_ok=True)
            if os.path.exists(dest):
                logger.warning(f"Migration: {dest} exists; skipping {src}.")
                continue
            os.replace(src, dest)
            migrated.append(dest)
            logger.info(f"Artifact organized -> {dest}")
        return migrated

    @staticmethod
    def citation_list(sources: list[str] | dict[str, str]) -> str:
        """Render a **Source Links / Citations** block for an artifact.

        Accepts either a list of source URLs or a ``{label: url}`` mapping. The
        block closes every artifact whose provenance tags use the ``[V#N]``
        convention (each number N resolves to the Nth entry here), so
        ``[V#3]`` -> ``[#3] <label> — <url>``.

        Returns a ready-to-append markdown string (leading + trailing newline
        included). Labels default to the bare URL when only a list is given.
        """
        if isinstance(sources, dict):
            items: list[tuple[str, str]] = list(sources.items())
        else:
            items = [(u, u) for u in sources]
        lines = ["\n## Source Links / Citations", ""]
        for i, (label, url) in enumerate(items, 1):
            lines.append(f"[#{i}] {label} — {url}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _drop_low_confidence(chunks: list[Chunk]) -> list[Chunk]:
        """EMIT hygiene: drop confidence='low' hits unless they are all we have
        (a lone low hit is still better than nothing)."""
        strong = [c for c in chunks
                  if c.metadata.get('confidence') != 'low']
        return strong or chunks

    async def research(self, question: str, out_path: str | None = None,
                       discover: int = 5, recall: int = 6,
                       strategy: str | None = None,
                       answer_first: bool | None = None) -> str | None:
        """Agentic research workflow: DISCOVER -> INGEST -> RECALL -> EMIT.

        Live web-searches the question (via the configured discovery provider),
        ingests the top-ranked sources into the vault, hybrid-retrieves the best
        chunks, and writes a grounding-context file. Returns the path written,
        or None if nothing was retrieved.

        strategy: "fast", "balanced", or "aggressive"; defaults to
            network.default_strategy from config. Controls the fetch chain used
            for both discovery and ingestion (e.g. "aggressive" enables the
            FlareSolverr path for anti-bot-protected sources).

        answer_first: when True (config research.answer_first, default), the
            existing vault is queried BEFORE any discovery; if a high-confidence
            memory hit answers the question, live discovery is skipped entirely
            (Adaptive-RAG-style routing — most recurring questions need no new
            retrieval). Pass False to always run live discovery.
        """
        if strategy is None:
            strategy = self.config.get("network.default_strategy", "aggressive")

        if answer_first is None:
            answer_first = bool(self.config.get('research.answer_first', True))

        # [0/ANSWER-FIRST] memory check before touching the web.
        memory_chunks: list[Chunk] = self.vault.search_vault(
            question, limit=recall, hybrid=True)
        memory_chunks = self._drop_low_confidence(memory_chunks)
        answered = (answer_first and memory_chunks and any(
            c.metadata.get('confidence') == 'high' for c in memory_chunks))

        if answered:
            print(f"\n[0/ANSWER-FIRST] memory answers the question; skipping DISCOVER", flush=True)
            chunks: list[Chunk] = memory_chunks
        else:
            print(f"\n[1/DISCOVER] searching web for: {question!r} (top {discover})", flush=True)
            await self._discover_and_ingest(question, discover, strategy, force_refresh=False)

            print(f"\n[2/RECALL] hybrid-retrieving top {recall} chunks", flush=True)
            chunks = self.vault.search_vault(question, limit=recall, hybrid=True)
            chunks = self._drop_low_confidence(chunks)
            if not chunks:
                print("  -> no chunks retrieved")
                return None

        out_path = self.resolve_artifact_out(out_path)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        print("\n[3/EMIT] writing grounding context", flush=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Grounding Context\n## Question\n{question}\n\n")
            if answered:
                f.write("> Answer-first recall: live DISCOVER was skipped "
                        "(existing high-confidence memory hit).\n\n")
            f.write(f"## Retrieved sources ({len(chunks)})\n\n")
            seen: set = set()
            for i, c in enumerate(chunks, 1):
                src = c.metadata.get("source_url", "?")
                seen.add(src)
                f.write(f"### [{i}] {src}  (score {c.metadata.get('hybrid_score', 0):.4f} | {c.metadata.get('confidence', 'n/a')})\n")
                f.write(f"{c.text}\n\n")
            f.write(f"## Distinct sources ingested: {len(seen)}\n")
            for s in sorted(seen):
                f.write(f" - {s}\n")
            f.write(self.citation_list(sorted(seen)))

        abs_path = os.path.abspath(out_path)
        print(f"\n=== DONE. {len(chunks)} chunks, {len(seen)} sources -> {abs_path}")
        return abs_path

    def verify_claim(self, claim: str) -> str:
        """Programmatic adversarial-audit: confirm a claim against the vault.

        Checks the raw stored chunk text for the claim and reports whether it is
        supported. Returns one of:
          "verified"   - the claim (or a distinctive normalized substring of it)
                         appears verbatim in a stored chunk.
          "partial"    - the vault has strong FTS5 keyword support for the
                         claim, but no verbatim match.
          "unverified" - no vault support for the claim.

        This makes the [V] honor-system tag machine-checkable: the caller can
        refuse to tag [V] unless this returns "verified".
        """
        if not claim or not claim.strip():
            return "unverified"

        # 1) Verbatim check against ALL stored chunk text (ground truth),
        #    not just the top retrieval hits. Normalize whitespace on BOTH
        #    sides: stored chunks may split a phrase across line breaks
        #    (e.g. "is \ndefined"), so a raw LIKE against a single-space
        #    needle would miss verbatim text. Instead, use the LIKE only as
        #    a cheap candidate pre-filter (with whitespace runs widened to %
        #    so newlines/multi-space pass) and confirm the exact normalized
        #    needle in Python.
        needle = re.sub(r"\s+", " ", claim.strip()).lower()
        candidates: list[str] = []
        with self.vault._db() as (_conn, cursor):
            # Slide a 60-char window across the needle so a claim whose
            # *distinctive* portion is not its first 60 chars still matches
            # verbatim (A1). Every windowed fragment is tested; the first hit
            # confirms the claim.
            step = 60
            window = 60
            # A short needle is one fragment; a long one is broken into
            # overlapping windows so a distinctive tail gets a chance.
            fragments = [needle[i:i + window]
                         for i in range(0, max(1, len(needle) - window + 1), step)]
            for fragment in fragments:
                if not fragment.strip():
                    continue
                # Escape LIKE wildcards in the fragment itself before widening
                # spaces; then widen whitespace runs to % so stored line breaks
                # pass the pre-filter.
                like_fragment = fragment.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
                like_fragment = re.sub(r"\s+", "%", like_fragment)
                cursor.execute(
                    "SELECT text FROM chunks_fts WHERE lower(text) LIKE ? ESCAPE '\\'",
                    (f"%{like_fragment}%",)
                )
                candidates = [row[0] for row in cursor.fetchall()]
                for raw in candidates:
                    if needle in re.sub(r"\s+", " ", raw.lower()):
                        return "verified"

        # 2) FTS5 keyword overlap: build a proper AND-of-phrases MATCH for the
        #    claim and measure the strength of the top hit. "Partial" is only
        #    reported when the TOP hit is a strong BM25 match (rank well into
        #    negative territory) — co-occurrence of a few common stopwords in
        #    unrelated boilerplate is NOT evidence (A2).
        fts = self.vault._fts_query(claim)
        if fts:
            with self.vault._db() as (_conn, cursor):
                cursor.execute(
                    "SELECT rank FROM chunks_fts WHERE chunks_fts MATCH ? "
                    "ORDER BY rank LIMIT 1",
                    (fts,)
                )
                row = cursor.fetchone()
                # FTS5 BM25 ranks are negative; a strong all-terms match scores
                # far below -2.0. Anything shallower is treated as coincidence.
                if row is not None and row[0] < -2.0:
                    return "partial"
        return "unverified"

    async def _process_document(self, url: str, strategy: str, force_refresh: bool) -> tuple[list[Chunk], dict[str, Any]]:
        """
        Core processing pipeline for a single URL.
        Returns (chunks, meta_overrides).
        """
        # Check cache
        if not force_refresh and self.vault.document_exists(url, self.config.get('cache.ttl_seconds', 86400)):
            logger.info(f"Cache HIT for {url} (in vault). Skipping network.")
            # Return empty chunks, but indicate cache hit
            return [], {"cached": True, "url": url}

        # Fetch
        try:
            text, binary, content_type = await self.fetcher.fetch(url, strategy)
        except RuntimeError as e:
            error_msg = str(e)
            if error_msg == "CF_COOKIE_EXPIRED":
                return [Chunk(
                    text="CF_COOKIE_EXPIRED: Your Cloudflare cookie has expired. Update 'auth.cookie_string' in hoardcore.toml.",
                    metadata={"source": url, "error": True, "code": "CF_COOKIE_EXPIRED"}
                )], {"error": "CF_COOKIE_EXPIRED"}
            elif error_msg == "FETCH_FAILED":
                return [Chunk(
                    text=f"Network error: All strategies failed for {url}.",
                    metadata={"source": url, "error": True, "status": "network_blocked"}
                )], {"error": "FETCH_FAILED"}
            raise

        # Save binary if applicable
        binary_path = None
        if binary and self.save_binary and content_type not in ['text/html', 'text/plain']:
            binary_path = self.vault.save_binary(url, content_type, binary)

        # Parse document
        markdown = ""
        parser_meta = {"content_type": content_type}

        if 'text/html' in content_type or 'text/plain' in content_type:
            if text is None and binary:
                text = binary.decode('utf-8', errors='ignore')
            if text:
                markdown, html_meta = await self.parser.clean_html(text, url)
                parser_meta.update(html_meta)
                parser_meta['source_type'] = 'html'
        elif binary:
            markdown, bin_meta = await self.parser.parse_binary(content_type, binary)
            parser_meta.update(bin_meta)
            parser_meta['source_type'] = 'binary'
            parser_meta['binary_path'] = binary_path

        if not markdown:
            markdown = "[No extractable content found]"

        # Quality assessment
        original_len = len(text) if text else (len(binary) if binary else 1)
        quality_score = len(markdown) / max(original_len, 1)
        parser_meta['quality_score'] = min(quality_score, 1.0)
        parser_meta['quality_label'] = 'high' if quality_score > 0.3 else ('medium' if quality_score > 0.1 else 'low')

        # Junk detection: refuse to persist boilerplate / empty extraction.
        # These otherwise pollute the FTS vault with garbage that drowns real results.
        junk_reason = self._detect_junk(markdown, text, parser_meta, quality_score)
        if junk_reason:
            logger.warning(f"Skipping index of {url} (junk: {junk_reason}).")
            return [Chunk(
                text=markdown,
                metadata={
                    "source": url,
                    "header_path": "",
                    "junk": True,
                    "junk_reason": junk_reason,
                    "quality_score": parser_meta['quality_score'],
                    "parser": parser_meta.get('parser', 'unknown')
                }
            )], {**parser_meta, "junk": True, "junk_reason": junk_reason}

        # Chunk
        chunks = await self.chunker.chunk(markdown, url, parser_meta)

        # Save extracted text to disk
        self.vault.save_extracted_text(url, markdown, chunks, parser_meta)

        # Route through the parallel pipeline when enabled (large batches only).
        self.vault.ingest_chunks_parallel(url, chunks, parser_meta)

        return chunks, parser_meta

    @staticmethod
    def _detect_junk(markdown: str, raw_text: str | None, parser_meta: dict[str, Any], quality_score: float) -> str | None:
        """Return a reason string if extraction is boilerplate/empty, else None."""
        stripped = markdown.strip()

        # Explicit empty-marker produced by the parser pipeline.
        if not stripped or "[No extractable content found]" in stripped:
            return "empty_extraction"

        # Generic block/redirect/captcha/consent pages masquerade as real content.
        boilerplate = [
            "Please click", "if you are not redirected",
            "are you having trouble accessing", "click here",
            "enable javascript", "robot check", "verify you are human",
            "cloudflare", "not a robot", "access denied",
            "the page you are looking for", "we couldn't find the page",
            "bad gateway", "404 not found", "the requested url was not found",
        ]
        lower = stripped.lower()
        matched = [b for b in boilerplate if b in lower]
        # Very short extracted body is almost always a mis-hit.
        if quality_score < 0.02 and len(stripped) < 60:
            return "near_empty_extraction"
        if matched and len(stripped) < 600:
            return f"boilerplate:{matched[0]}"

        # Some failures leave a low-score body that is still real (PDF ratio is naturally low);
        # rely on structural signals above, not raw length ratio alone.
        return None

    async def _scrape_single(self, url: str, strategy: str, force_refresh: bool) -> list[Chunk]:
        """Scrape a single URL."""
        chunks, meta = await self._process_document(url, strategy, force_refresh)
        if meta.get('cached'):
            # Cache hit: the pipeline fetches nothing, so serve the vaulted
            # chunks back to the caller instead of an empty result.
            return self.vault.get_chunks_for_url(url)
        return chunks

    async def _crawl_domain(self, url: str, strategy: str, force_refresh: bool) -> list[Chunk]:
        """Crawl an entire domain using sitemap."""
        all_chunks = []
        discovered_urls = await self.crawler.discover_urls(url)

        if not discovered_urls:
            logger.warning(f"No URLs discovered for {url}. Falling back to single scrape.")
            return await self._scrape_single(url, strategy, force_refresh)

        logger.info(f"Discovered {len(discovered_urls)} URLs for crawling.")

        # Use semaphore to limit parallel workers
        max_workers = self.config.get('crawler.parallel_workers', 5)
        semaphore = asyncio.Semaphore(max_workers)

        async def _crawl_one(single_url: str) -> list[Chunk]:
            async with semaphore:
                try:
                    chunks, _ = await self._process_document(single_url, strategy, force_refresh)
                    if chunks and not chunks[0].metadata.get('error'):
                        all_chunks.extend(chunks)
                    return chunks
                except Exception as e:
                    logger.error(f"Failed to crawl {single_url}: {e}")
                    return []

        tasks = [_crawl_one(u) for u in discovered_urls]
        await asyncio.gather(*tasks, return_exceptions=True)

        return all_chunks

    async def fetch(
        self,
        url: str,
        action: str = "scrape",
        strategy: str | None = None,
        query: str | None = None,
        force_refresh: bool = False,
        urls: list[str] | None = None,
        max_results: int = 0,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Public entry point.

        Args:
            url: Target URL or domain.
            action: "scrape", "crawl", "search", "ingest", or "discover".
            strategy: "fast", "balanced", or "aggressive".
            query: Required for "search" and "discover" actions.
            force_refresh: Ignore cache and re-fetch.
            urls: For "ingest", an explicit list of URLs to process in parallel.
            max_results: For "discover", how many search results to ingest.
            mode: For "search", "fast" (FTS-only) or "hybrid" (force vector+RFF).

        Returns:
            List of dicts with "text" and "metadata" for the LLM.
        """
        if strategy is None:
            strategy = self.config.get('network.default_strategy', 'aggressive')

        # Route actions
        if action == "search":
            if not query:
                return [{
                    "text": "Error: 'query' parameter required for action='search'.",
                    "metadata": {"error": True}
                }]
            limit = self.config.get('indexer.search_limit', 20)
            domain = urlparse(url).netloc or None
            hybrid: bool | None = None
            if mode == 'fast':
                hybrid = False
            elif mode == 'hybrid':
                hybrid = True
            chunks = self.vault.search_vault(query, limit, domain=domain, hybrid=hybrid)
            return [c.to_dict() for c in chunks]

        elif action == "ingest":
            # Explicit URL list (comma/whitespace separated). Closes the
            # discovery gap by letting callers feed curatined URLs directly.
            if not urls:
                return [{
                    "text": "Error: 'urls' parameter required for action='ingest'.",
                    "metadata": {"error": True}
                }]
            return await self._ingest_many(urls, strategy, force_refresh)

        elif action == "discover":
            # Live web discovery: query -> ranked URLs -> parallel ingest.
            # Closes the discovery gap WITHOUT an API key by going through the
            # same resilient fetch chain (incl. FlareSolverr) as the crawler.
            if not query:
                return [{
                    "text": "Error: 'query' parameter required for action='discover'.",
                    "metadata": {"error": True}
                }]
            return await self._discover_and_ingest(query, max_results, strategy, force_refresh)

        elif action == "crawl":
            chunks = await self._crawl_domain(url, strategy, force_refresh)
            return [c.to_dict() for c in chunks if not c.metadata.get('error', False)]

        else:  # "scrape" (default)
            chunks = await self._scrape_single(url, strategy, force_refresh)
            return [c.to_dict() for c in chunks]

    async def _ingest_many(self, urls: list[str], strategy: str, force_refresh: bool) -> list[dict[str, Any]]:
        """Process an explicit list of URLs with a bounded-worker pool."""
        max_workers = self.config.get('crawler.parallel_workers', 5)
        semaphore = asyncio.Semaphore(max_workers)
        results: list[dict[str, Any]] = []

        async def _ingest_one(target: str) -> None:
            async with semaphore:
                try:
                    chunks, meta = await self._process_document(target, strategy, force_refresh)
                    if meta.get('error'):
                        if chunks:
                            results.append(chunks[-1].to_dict())
                        return
                    if not meta.get('junk'):
                        results.extend(c.to_dict() for c in chunks)
                except Exception as e:
                    logger.error(f"Failed to ingest {target}: {e}")
                    results.append({
                        "text": f"Error ingesting {target}: {e}",
                        "metadata": {"source": target, "error": True}
                    })

        await asyncio.gather(*[_ingest_one(u) for u in urls], return_exceptions=True)
        return results

    async def _discover_and_ingest(self, query: str, max_results: int,
                                   strategy: str, force_refresh: bool) -> list[dict[str, Any]]:
        """Run a live web search, then ingest the top-ranked URLs.

        Uses the free DuckDuckGo HTML provider through the existing
        fetch chain, so FlareSolverr is applied automatically when a search
        result is anti-bot protected. Returns the ingested chunks.
        """
        cfg_max = self.config.get('discovery.max_results', 10)
        cfg_top = self.config.get('discovery.top_rank', 6)
        limit = max_results if max_results > 0 else cfg_max

        results = await self.discovery.search(query, max_results=limit, strategy=strategy)
        if not results:
            return [{
                "text": f"No URLs discovered for query: {query!r}.",
                "metadata": {"source": "discovery", "error": True, "query": query}
            }]

        logger.info(f"Discovered {len(results)} URLs for query: {query!r}")

        # rank-biased ingest: take the top-N results (configurable)
        targets = [r.url for r in results[:cfg_top]]
        summary = [{
            "text": f"[discovery] top {len(targets)} URLs for {query!r}",
            "metadata": {
                "source": "discovery", "query": query,
                "candidates": [r.__dict__ for r in results]
            }
        }]

        ingested = await self._ingest_many(targets, strategy, force_refresh)
        return summary + ingested

# =============================================================================
# 9. CLI ENTRYPOINT
# =============================================================================

_EXAMPLES = (
    "\nExamples:\n"
    "  python hoardcore.py https://example.com --action scrape\n"
    "  python hoardcore.py https://docs.python.org --action crawl --strategy aggressive\n"
    "  python hoardcore.py https://arxiv.org/abs/2110.12345.pdf --action scrape\n"
    "  python hoardcore.py https://example.com --action search --query 'machine learning'\n"
    "  python hoardcore.py _ --action ingest --urls 'u1,u2,u3'\n"
    "  python hoardcore.py _ --action discover --query 'negros renewable energy' --limit 5\n"
    "  python hoardcore.py _ --action research --query 'how does bokashi compost' --discover 5 --recall 6\n"
    "  python hoardcore.py _ --action research --query 'negros economy' --out artifacts/report.md\n"
    "  python hoardcore.py _ --action research --query 'sleep research' --vault sleep\n"
    "  python hoardcore.py _ --action verify --claim 'the Epoch doubling time is 6 months'\n"
    "  python hoardcore.py _ --action check   # verify vault integrity\n"
)


def _build_parser() -> argparse.ArgumentParser:
    """Argument parser for the hoardcore CLI (D3): typed, --help, no silent
    typo tolerance. `url` is positional so legacy `hoardcore.py <URL>` calls,
    and the `_` placeholder for vault-only actions, keep working verbatim."""
    parser = argparse.ArgumentParser(
        prog="hoardcore",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EXAMPLES,
        # Reject prefix abbreviations so a typo like `--recal` fails loudly
        # instead of silently matching --recall (D3).
        allow_abbrev=False,
    )
    parser.add_argument(
        "url", nargs="?", default="_",
        help="Target URL or domain, or '_' for vault-only actions.",
    )
    parser.add_argument(
        "--action", choices=["scrape", "crawl", "search", "ingest",
                             "discover", "research", "verify", "check"],
        default="scrape", help="Action to run (default: scrape).",
    )
    parser.add_argument(
        "--strategy", choices=["fast", "balanced", "aggressive"], default=None,
        help="Fetch strategy (default: network.default_strategy).",
    )
    parser.add_argument("--query", default=None, help="Search/research query.")
    parser.add_argument("--claim", default=None, help="Claim to verify.")
    parser.add_argument("--discover", type=int, default=None,
                        help="Results to discover+ingest for research.")
    parser.add_argument("--recall", type=int, default=6,
                        help="Chunks to recall for research.")
    parser.add_argument("--out", default=None, dest="out_path",
                        help="Artifact output path for research.")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the cache and re-fetch.")
    parser.add_argument("--urls", default=None,
                        help="Comma/space-separated URL list for ingest.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max results for discover/search.")
    parser.add_argument("--vault", default=None, dest="vault_name",
                        help="Per-topic vault name.")
    parser.add_argument("--migrate", action="store_true",
                        help="With --action check: rebuild vault at 16 KB pages.")
    parser.add_argument("--mode", choices=["fast", "hybrid"], default=None,
                        help="Force search mode (FTS-only vs vector+RRF).")
    parser.add_argument("--no-answer-first", action="store_true",
                        help="With --action research: always run live DISCOVER, "
                             "even if the existing vault has a high-confidence answer.")
    return parser


async def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    url = args.url
    action = args.action
    strategy = args.strategy
    query = args.query
    claim = args.claim
    force_refresh = args.force
    urls = re.split(r'[,\s]+', args.urls.strip()) if args.urls else None
    urls = [u for u in urls if u] if urls else None
    max_results = args.limit
    discover = args.discover
    recall = args.recall
    out_path = args.out_path
    vault_name = args.vault_name
    migrate_page_size = args.migrate
    mode = args.mode

    scraper = HoardCore(vault_name=vault_name)
    print(f"\n🚀 HoardCore v{__version__}: Action={action}, URL={url}, Strategy={strategy or 'default'}")
    organized = scraper.organize_artifacts_by_day()
    if organized:
        print(f"   📂 Organized {len(organized)} artifact(s) into day folders")
    print(f"   📁 Vault: {scraper.vault.root_dir}/vault.db | 🏛 Artifacts: {scraper.artifacts_dir}/")

    if action == "research":
        if not query:
            print("  ⚠️  --query required for --action research", file=sys.stderr)
            sys.exit(2)
        written = await scraper.research(query, out_path=out_path,
                                         discover=discover or 5, recall=recall,
                                         strategy=strategy,
                                         answer_first=not args.no_answer_first)
        sys.exit(0 if written else 1)

    if action == "verify":
        if not claim:
            print("  ⚠️  --claim required for --action verify", file=sys.stderr)
            sys.exit(2)
        result = scraper.verify_claim(claim)
        print(f"VERIFY: {result.upper()}")
        print(f"claim: {claim}")
        # exit codes: 0=verified, 1=partial, 2=unverified (CI-wireable)
        sys.exit(0 if result == "verified" else (1 if result == "partial" else 2))

    if action == "check":
        if migrate_page_size:
            migrated = scraper.vault.migrate_page_size()
            print(f"  🔧 Page size: {'migrated to 16 KB' if migrated else 'already at target'}")
        ok = scraper.vault.verify_vault()
        sys.exit(0 if ok else 1)

    result = await scraper.fetch(
        url, action=action, strategy=strategy,
        query=query, force_refresh=force_refresh,
        urls=urls if action == "ingest" else None,
        max_results=max_results, mode=mode
    )

    print("\n" + "=" * 80)
    print(f"✅ Done. Returned {len(result)} chunks.")

    # Preview
    for i, chunk in enumerate(result[:3]):
        print(f"\n--- CHUNK {i+1} ---")
        print(f"Metadata: {chunk['metadata']}")
        preview = chunk['text'][:300] + "..." if len(chunk['text']) > 300 else chunk['text']
        print(f"Preview: {preview}")

    if len(result) > 3:
        print(f"\n... and {len(result) - 3} more chunks.")

if __name__ == "__main__":
    asyncio.run(main())
else:

    def cli_entry() -> None:
        """Synchronous console-script entry point (setuptools)."""
        asyncio.run(main())
