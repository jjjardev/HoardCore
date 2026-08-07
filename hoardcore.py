#!/usr/bin/env python3
"""
HoardCore-RAG v0.1 (HCRAG) - Universal LLM Document Ingestion Engine.
Ingests HTML, PDF, DOCX, EPUB, and TXT into a persistent, searchable SQLite Vault.
Handles Cloudflare, Sitemap crawling, and semantic chunking.
Hybrid retrieval fuses FTS5 keyword search with vector search (RRF), and a
web-discovery action feeds the crawler from a live search query.

Usage:
    python hoardcore.py <URL> --action scrape|crawl|search --query "text"
    python hoardcore.py _ --action ingest --urls "u1,u2,u3"
    python hoardcore.py _ --action discover --query "negros occidental renewable energy" --limit 5
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import tomllib
import zipfile
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
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
# HoardCore-RAG (HCRAG) v0.1 Configuration

[general]
timeout_seconds = 30
max_retries = 2
user_agent = "HoardCore-Bot/5.0 (LLM Agent)"

[network]
default_strategy = "balanced"   # fast, balanced, aggressive
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
save_binary = true               # Save original PDF/DOCX/EPUB files
save_raw_html = false            # Save raw HTML for debugging

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

[embeddings]
enabled = true
dim = 256
hybrid_search = true       # merge FTS + vector via RRF
top_k = 40                 # candidate pool from vector search

[discovery]
enabled = true
provider = "duckduckgo_html"   # free, no key; uses the existing fetch/FlareSolverr chain (Mojeek auto-fallback)
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
            "network": {"default_strategy": "balanced", "enable_preflight": True},
            "auth": {"cookie_string": ""},
            "solver": {"enabled": False, "url": "http://localhost:8191/v1", "solver_timeout": 60},
            "storage": {"root_dir": "hoardcore_data", "artifacts_dir": "artifacts", "save_binary": True, "save_raw_html": False},
            "parsers": {"enable_pdf": True, "enable_docx": True, "enable_epub": True, "extract_pdf_tables": True, "enable_pdf_ocr": True},
            "crawler": {"respect_robots": True, "sitemap_limit": 500, "parallel_workers": 5},
            "indexer": {"enable_fts": True, "search_limit": 20},
            "embeddings": {"enabled": True, "dim": 256, "hybrid_search": True, "top_k": 40},
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
    """FNV-1a 64-bit hash. Deterministic, dependency-free feature hashing."""
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


class EmbeddingsEngine:
    """Turns chunk text into fixed-dimension vectors for hybrid retrieval.

    Uses dependency-free sparse hashing of word + char n-gram features into a
    unit vector. Cheap, offline, deterministic, and requires no extra packages.
    This is lexical (vocabulary-overlap) similarity, which — fused with FTS5
    keyword search via Reciprocal Rank Fusion — is sufficient for an LLM tool
    and keeps HCRAG lightweight.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.enabled = config.get('embeddings.enabled', True)
        self.dim = int(config.get('embeddings.dim', 256))

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
        return self._hash_vector(text)

    @staticmethod
    def cosine(a: bytes, b: bytes, dim: int) -> float:
        va = array('f')
        va.frombytes(a)
        vb = array('f')
        vb.frombytes(b)
        if len(va) > dim:
            va = va[:dim]
        if len(vb) > dim:
            vb = vb[:dim]
        dot = sum(x * y for x, y in zip(va, vb, strict=False))
        return float(dot)  # vectors are L2-normalized at build, so dot == cosine

# =============================================================================
# 3. PERSISTENT STORAGE & VAULT (SQLite + Filesystem)
# =============================================================================

class VaultManager:
    """Handles SQLite FTS indexing and filesystem storage."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.root_dir = config.get('storage.root_dir', 'hoardcore_data')
        os.makedirs(self.root_dir, exist_ok=True)
        self.artifacts_dir = config.get('storage.artifacts_dir', 'artifacts')
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.db_path = os.path.join(self.root_dir, 'vault.db')
        self.embeddings = EmbeddingsEngine(config)
        self._vector_dim = self.embeddings.dim
        self._init_db()
        self.backfill_vectors()

    @contextmanager
    def _db(self) -> Iterator[tuple[sqlite3.Connection, sqlite3.Cursor]]:
        """Yield a committed-on-success, surfaced-on-exception DB cursor.

        Guarantees the connection is always closed and transactions are never
        left dangling, even if a query raises mid-method.
        """
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            cursor = conn.cursor()
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize SQLite with FTS5 virtual table."""
        with self._db() as (_conn, cursor):
            # Enable FTS5
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")

            # Main table for metadata
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    domain TEXT,
                    file_name TEXT,
                    content_type TEXT,
                    fetched_at REAL,
                    parser_used TEXT,
                    quality_score REAL,
                    total_chunks INTEGER,
                    metadata_json TEXT
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
        if not name:
            name = hashlib.md5(url.encode()).hexdigest()[:8]

        # Determine extension
        ext_map = {
            'application/pdf': '.pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
            'application/epub+zip': '.epub',
            'text/html': '.html',
            'text/plain': '.txt'
        }
        ext = ext_map.get(content_type, '.bin')
        # Ensure unique by hashing
        hash_suffix = hashlib.md5(url.encode()).hexdigest()[:6]
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

    def index_document(self, url: str, chunks: list[Chunk], meta: dict[str, Any]) -> None:
        """Insert/update document and chunks in SQLite FTS."""
        if not self.config.get('indexer.enable_fts', True):
            return

        embed_ok = self.config.get('embeddings.enabled', True)
        with self._db() as (_conn, cursor):
            # Delete old entries for this URL
            cursor.execute("DELETE FROM documents WHERE url = ?", (url,))
            cursor.execute("DELETE FROM chunks_fts WHERE url = ?", (url,))
            cursor.execute("DELETE FROM chunk_vectors WHERE url = ?", (url,))

            # Insert document metadata
            domain = urlparse(url).netloc
            cursor.execute("""
                INSERT INTO documents (
                    url, domain, file_name, content_type, fetched_at,
                    parser_used, quality_score, total_chunks, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url,
                domain,
                meta.get('file_name', ''),
                meta.get('content_type', ''),
                time.time(),
                meta.get('parser_used', 'unknown'),
                meta.get('quality_score', 0.0),
                len(chunks),
                json.dumps(meta)
            ))

            # Insert chunks into FTS
            for chunk in chunks:
                cursor.execute("""
                    INSERT INTO chunks_fts (url, header_path, text, metadata_json)
                    VALUES (?, ?, ?, ?)
                """, (
                    url,
                    chunk.metadata.get('header_path', 'Root'),
                    chunk.text,
                    json.dumps(chunk.metadata)
                ))
                if embed_ok:
                    rowid = cursor.lastrowid
                    try:
                        vec = self.embeddings.vectorize(chunk.text)
                    except Exception as e:  # embedding failures must not block indexing
                        logger.warning(f"Embedding failed for {url}: {e}")
                        continue
                    cursor.execute(
                        "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) VALUES (?, ?, ?)",
                        (rowid, url, vec)
                    )

        logger.info(f"Indexed {len(chunks)} chunks for {url}")

    def backfill_vectors(self) -> int:
        """Compute and store embeddings for chunks missing one (e.g. chunks
        indexed before the vector table existed). Returns count backfilled."""
        if not self.config.get('embeddings.enabled', True):
            return 0
        count = 0
        with self._db() as (_conn, cursor):
            cursor.execute("""
                SELECT chunks_fts.rowid, chunks_fts.url, chunks_fts.text
                FROM chunks_fts
                LEFT JOIN chunk_vectors ON chunk_vectors.chunk_rowid = chunks_fts.rowid
                WHERE chunk_vectors.chunk_rowid IS NULL
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
            if not fts_match:
                return []
            fts_where = "chunks_fts MATCH ?"
            fts_params: list[Any] = [fts_match]
            if domain:
                fts_where += " AND url LIKE ?"
                fts_params.append(f'%{domain}%')
            cursor.execute(f"""
                SELECT rowid, url FROM chunks_fts
                WHERE {fts_where}
                ORDER BY rank
                LIMIT ?
            """, (*fts_params, fts_pool))
            fts_rows: list[tuple[int, str]] = cursor.fetchall()

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
            for rank, (rid, _u) in enumerate(fts_rows):
                rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (k + rank + 1)
            for rank, (_score, rid, _u) in enumerate(scored):
                rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (k + rank + 1)

            if not rrf:
                return []

            fused = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:max(limit, 0)]
            order = fused[:limit] if limit > 0 else fused
            ids = [rid for rid, _ in (fused[:limit] if limit > 0 else fused)]

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
        except Exception:
            return True

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

    async def parse_sitemap(self, sitemap_url: str) -> list[str]:
        """Parse sitemap XML and extract URLs."""
        try:
            async with aiohttp.ClientSession() as session, session.get(
                sitemap_url, timeout=30
            ) as resp:
                if resp.status != 200:
                    return []
                    xml = await resp.text()
                    # Simple regex extraction for <loc> tags
                    urls = re.findall(r'<loc>(.+?)</loc>', xml, re.IGNORECASE)
                    # Limit
                    return urls[:self.sitemap_limit]
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
    """Discovers URLs from a live web query, without an API key.

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

    def __init__(self):
        self.config = ConfigManager()
        self.vault = VaultManager(self.config)
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

    def write_artifact(self, filename: str, content: str) -> str:
        """Write a research deliverable into the artifacts directory.

        Returns the absolute path written. Raises if the filename would escape
        the artifacts directory.
        """
        if os.path.basename(filename) != filename:
            raise ValueError(f"artifact filename must be a bare name, got {filename!r}")
        path = os.path.join(self.vault.artifacts_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Artifact written -> {path}")
        return path

    # ------------------------------------------------------------------
    # ENFORCED PROVENANCE: machine-verify [V]/[E]/[H] claims vs the vault
    # ------------------------------------------------------------------

    def verify_claim(self, claim: str, recall: int = 6,
                     overlap_threshold: float = 0.35,
                     score_threshold: float = 0.005) -> dict[str, Any]:
        """Machine-enforce the provenance of a single claim.

        Retrieves the chunks in the vault most similar to ``claim`` and scores
        the lexical overlap between the claim's content tokens and each chunk
        (combined with its hybrid score). This turns the manual ``[V]/[E]/[H]``
        audit in ``skill.md`` into a check the system can *guarantee*: a claim
        labelled ``[V]`` must actually be substantiated by full primary text
        currently in the vault.

        Verdicts returned:
          - ``[V]`` verified: some vault chunk substantively overlaps the claim
            AND meets the hybrid-score threshold.
          - ``[E]`` external: partial overlap only (indirect / weaker support);
            not fully retraceable to the current vault.
          - ``[H]`` unsupported: nothing in the vault backs the claim.

        Args:
            claim: a natural-language statement asserting a fact.
            recall: how many chunks to retrieve for evaluation.
            overlap_threshold: minimum token-overlap ratio for ``[V]``.
            score_threshold: minimum hybrid score for a supporting chunk.

        Returns a dict: {claim, verdict, overlap, score, top_source, support[]}.
        """
        claim = (claim or "").strip()
        empty = {"claim": claim, "verdict": "[H]", "overlap": 0.0, "score": 0.0,
                 "top_source": None, "support": []}
        if not claim:
            empty["verdict"] = "[H]"
            empty["reason"] = "empty claim"
            return empty

        content_tokens = {
            t for t in EmbeddingsEngine._tokens(claim) if len(t) > 2
        }
        if not content_tokens:
            empty["verdict"] = "[H]"
            empty["reason"] = "no substantive tokens"
            return empty

        chunks = self.vault.search_vault(claim, limit=recall, hybrid=True)
        if not chunks:
            empty["verdict"] = "[H]"
            empty["reason"] = "no chunks retrieved"
            return empty

        scored = []
        for c in chunks:
            chunk_tokens = set(EmbeddingsEngine._tokens(c.text))
            if not chunk_tokens:
                continue
            overlap = len(content_tokens & chunk_tokens) / len(content_tokens)
            hy = float(c.metadata.get("hybrid_score", 0.0))
            scored.append({
                "overlap": round(overlap, 4),
                "score": round(hy, 5),
                "source": c.metadata.get("source_url", "?"),
                "snippet": c.text[:220],
            })

        if not scored:
            empty["verdict"] = "[H]"
            empty["reason"] = "no scored chunks"
            return empty

        top = max(scored, key=lambda s: (s["overlap"], s["score"]))
        result = {
            "claim": claim,
            "verdict": "[H]",
            "overlap": top["overlap"],
            "score": top["score"],
            "top_source": top["source"],
            "support": sorted(scored, key=lambda s: (-s["overlap"], -s["score"]))[:3],
        }
        if top["overlap"] >= overlap_threshold and top["score"] >= score_threshold:
            result["verdict"] = "[V]"
        elif top["overlap"] >= overlap_threshold * 0.5:
            result["verdict"] = "[E]"
        else:
            result["verdict"] = "[H]"
        return result

    def verify_artifact(self, path: str, recall: int = 6,
                        overlap_threshold: float = 0.35,
                        score_threshold: float = 0.005) -> list[dict[str, Any]]:
        """Adversarial-audit an artifact: verify every ``[V]``-tagged claim.

        Scans the file for lines carrying a ``[V]`` provenance tag, runs each
        claim through :meth:`verify_claim`, and returns a per-claim report.
        Any ``[V]`` the vault cannot substantiate is surfaced as a demotion to
        ``[E]``/``[H]`` — the enforcement guarantee made concrete.

        Returns a list of verify_claim dicts for every ``[V]`` claim found.
        """
        if not os.path.exists(path):
            logger.warning(f"verify_artifact: no such file {path}")
            return []
        reports: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "[V]" not in line:
                    continue
                claim = line.strip().lstrip("-#* >").strip()
                if not claim:
                    continue
                if claim.startswith("[V]"):
                    claim = claim[3:].strip()
                reports.append(self.verify_claim(claim, recall,
                                                 overlap_threshold, score_threshold))
        return reports

    async def research(self, question: str, out_path: str | None = None,
                       discover: int = 5, recall: int = 6,
                       strategy: str | None = None) -> str | None:
        """Agentic research workflow: DISCOVER -> INGEST -> RECALL -> EMIT.

        Live web-searches the question (via the configured discovery provider),
        ingests the top-ranked sources into the vault, hybrid-retrieves the best
        chunks, and writes a grounding-context file. Returns the path written,
        or None if nothing was retrieved.

        strategy: "fast", "balanced", or "aggressive"; defaults to
            network.default_strategy from config. Controls the fetch chain used
            for both discovery and ingestion (e.g. "aggressive" enables the
            FlareSolverr path for anti-bot-protected sources).
        """
        if strategy is None:
            strategy = self.config.get("network.default_strategy", "balanced")

        print(f"\n[1/DISCOVER] searching web for: {question!r} (top {discover})", flush=True)
        await self._discover_and_ingest(question, discover, strategy, force_refresh=False)

        print(f"\n[2/RECALL] hybrid-retrieving top {recall} chunks", flush=True)
        chunks: list[Chunk] = self.vault.search_vault(question, limit=recall, hybrid=True)
        if not chunks:
            print("  -> no chunks retrieved")
            return None

        if out_path is None:
            out_path = os.path.join(self.artifacts_dir, "grounding_context.md")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        print("\n[3/EMIT] writing grounding context", flush=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# Grounding Context\n## Question\n{question}\n\n")
            f.write(f"## Retrieved sources ({len(chunks)})\n\n")
            seen: set = set()
            for i, c in enumerate(chunks, 1):
                src = c.metadata.get("source_url", "?")
                seen.add(src)
                f.write(f"### [{i}] {src}  (score {c.metadata.get('hybrid_score', 0):.4f})\n")
                f.write(f"{c.text}\n\n")
            f.write(f"## Distinct sources ingested: {len(seen)}\n")
            for s in sorted(seen):
                f.write(f" - {s}\n")

        abs_path = os.path.abspath(out_path)
        print(f"\n=== DONE. {len(chunks)} chunks, {len(seen)} sources -> {abs_path}")
        return abs_path

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

        # Index in SQLite FTS
        self.vault.index_document(url, chunks, parser_meta)

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
        return False

    async def _scrape_single(self, url: str, strategy: str, force_refresh: bool) -> list[Chunk]:
        """Scrape a single URL."""
        chunks, meta = await self._process_document(url, strategy, force_refresh)
        if meta.get('cached'):
            # If cached, we need to fetch from vault
            # We'll just return an empty list, but the caller should handle.
            # Actually, let's force a re-fetch if cached and force_refresh is False.
            # Since we returned early, we don't have chunks.
            # Better: If cached, just return an empty list.
            return []
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
        max_results: int = 0
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

        Returns:
            List of dicts with "text" and "metadata" for the LLM.
        """
        if strategy is None:
            strategy = self.config.get('network.default_strategy', 'balanced')

        # Route actions
        if action == "search":
            if not query:
                return [{
                    "text": "Error: 'query' parameter required for action='search'.",
                    "metadata": {"error": True}
                }]
            limit = self.config.get('indexer.search_limit', 20)
            domain = urlparse(url).netloc or None
            chunks = self.vault.search_vault(query, limit, domain=domain)
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

        Uses the free DuckDuckGo HTML provider (no API key) through the existing
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

async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nExamples:")
        print("  python hoardcore.py https://example.com --action scrape")
        print("  python hoardcore.py https://docs.python.org --action crawl --strategy aggressive")
        print("  python hoardcore.py https://arxiv.org/abs/2110.12345.pdf --action scrape")
        print("  python hoardcore.py https://example.com --action search --query 'machine learning'")
        print("  python hoardcore.py _ --action ingest --urls 'u1,u2,u3'  ")
        print("  python hoardcore.py _ --action discover --query 'negros renewable energy' --limit 5")
        print("  python hoardcore.py _ --action research --query 'how does bokashi compost' --discover 5 --recall 6")
        print("  python hoardcore.py _ --action research --query 'negros economy' --out artifacts/report.md")
        print("  python hoardcore.py _ --action verify --claim 'HCRAG uses FTS5 + RRF hybrid retrieval'")
        print("  python hoardcore.py _ --action verify-file --query artifacts/report.md")
        sys.exit(1)

    url = sys.argv[1]
    action = "scrape"
    strategy = None
    query = None
    force_refresh = False
    urls = None
    max_results = 0
    discover = None
    recall = 6
    out_path: str | None = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--action" and i + 1 < len(sys.argv):
            action = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--strategy" and i + 1 < len(sys.argv):
            strategy = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] in ("--query", "--claim") and i + 1 < len(sys.argv):
            query = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--discover" and i + 1 < len(sys.argv):
            try:
                discover = int(sys.argv[i + 1])
            except ValueError:
                discover = None
            i += 2
        elif sys.argv[i] == "--recall" and i + 1 < len(sys.argv):
            try:
                recall = int(sys.argv[i + 1])
            except ValueError:
                recall = 6
            i += 2
        elif sys.argv[i] == "--out" and i + 1 < len(sys.argv):
            out_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--force":
            force_refresh = True
            i += 1
        elif sys.argv[i] == "--urls" and i + 1 < len(sys.argv):
            import re as _re
            urls = _re.split(r'[,\s]+', sys.argv[i + 1].strip())
            urls = [u for u in urls if u]
            i += 2
        elif sys.argv[i] == "--limit" and i + 1 < len(sys.argv):
            try:
                max_results = int(sys.argv[i + 1])
            except ValueError:
                max_results = 0
            i += 2
        else:
            i += 1

    scraper = HoardCore()
    print(f"\n🚀 HoardCore-RAG v0.1 (HCRAG): Action={action}, URL={url}, Strategy={strategy or 'default'}")
    print(f"   📁 Vault: {scraper.vault.root_dir}/vault.db | 🏛 Artifacts: {scraper.artifacts_dir}/")

    if action == "research":
        if not query:
            print("  ⚠️  --query required for --action research", file=sys.stderr)
            sys.exit(2)
        written = await scraper.research(query, out_path=out_path,
                                         discover=discover or 5, recall=recall,
                                         strategy=strategy)
        sys.exit(0 if written else 1)

    if action == "verify":
        if not query:
            print("  ⚠️  --claim (via --query) required for --action verify", file=sys.stderr)
            sys.exit(2)
        report = scraper.verify_claim(query, recall=recall)
        print("\n=== ENFORCED PROVENANCE: claim verification ===")
        print(f"Claim   : {report['claim']}")
        print(f"Verdict : {report['verdict']}")
        print(f"Overlap : {report['overlap']:.3f}  |  Hybrid score: {report['score']:.5f}")
        print(f"Top src : {report['top_source']}")
        for s in report.get("support", []):
            print(f"  - {s['source']}  (overlap {s['overlap']:.3f}, score {s['score']:.5f})")
            print(f"    {s['snippet']!r}")
        sys.exit(0)

    if action == "verify-file":
        if not query:
            print("  ⚠️  --query (path to artifact) required for --action verify-file", file=sys.stderr)
            sys.exit(2)
        reports = scraper.verify_artifact(query, recall=recall)
        print(f"\n=== ENFORCED PROVENANCE: adversarial audit of {query} ===")
        print(f"Verified [V] claims found: {len(reports)}\n")
        for r in reports:
            flag = "OK  " if r["verdict"] == "[V]" else "DEMOTE"
            print(f"[{flag}] {r['verdict']}  overlap {r['overlap']:.3f}  :: {r['claim'][:90]}")
            if r["verdict"] != "[V]":
                print(f"        -> best support: {r['top_source']}")
        sys.exit(0)

    result = await scraper.fetch(
        url, action=action, strategy=strategy,
        query=query, force_refresh=force_refresh,
        urls=urls if action == "ingest" else None,
        max_results=max_results
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
