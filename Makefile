# Makefile for HoardCore - Research Toolkit for AI Agents
# Usage:
#   make install   - Install all dependencies (including heavy PDF/DOCX parsers)
#   make run       - Run a quick test scrape
#   make discover  - Web-search + ingest (feed the crawl from a live query)
#   make test      - Hybrid-search smoke test
#   make clean     - Remove cached data, vault, and Python cache
#
# Set PYTHON to override the interpreter (defaults to python3.11).

.PHONY: install run discover test bench clean lint audit typecheck coverage check

PYTHON ?= python3.11

install:
	@echo "🔧 Installing HoardCore into a virtualenv..."
	@test -d venv || $(PYTHON) -m venv venv
	venv/bin/python -m pip install --upgrade pip
	venv/bin/python -m pip install aiohttp curl_cffi trafilatura readability-lxml tomli PyMuPDF python-docx ebooklib lxml fastembed
	@echo "For OCR of scanned PDFs, install the optional fallback next:"
	@echo "  venv/bin/python -m pip install rapidocr_onnxruntime"
	@echo "✅ Lightweight install complete. Dense retrieval (ONNX, no torch) is on by default."

run:
	@echo "🚀 Testing HoardCore on a sample URL..."
	venv/bin/python hoardcore.py https://example.com --action scrape

discover:
	@echo "🔎 Searching the web and ingesting top results..."
	venv/bin/python hoardcore.py "_" --action discover --query "nothing here -- pass a query like 'negros renewable energy'"

test:
	@echo "🧪 Running HoardCore test suite (pytest)..."
	venv/bin/python -m pytest tests/ -q

lint:
	@echo "🧼 Ruff lint..."
	venv/bin/python -m ruff check hoardcore.py tests/

audit:
	@echo "🔐 Bandit security scan..."
	venv/bin/bandit -q hoardcore.py

typecheck:
	@echo "🛡️ Pyright type check..."
	venv/bin/pyright hoardcore.py --pythonpath venv/bin/python

coverage:
	@echo "📊 Coverage report (fail under 66%)..."
	venv/bin/python -m pytest tests/ -q --cov=hoardcore --cov-report=term-missing

check: lint audit typecheck coverage
	@echo "✅ All gates green."

bench:
	@echo "📐 Running vector-search benchmark (float32 vs int8 x page size)..."
	venv/bin/python tools/bench_vector.py

clean:
	@echo "🧹 Cleaning up HoardCore data..."
	rm -rf hoardcore_data/
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	rm -f hoardcore.toml
	@echo "✅ Clean complete."