# Makefile for HoardCore (HCH) - Research Toolkit for AI Agents
# Usage:
#   make install   - Install all dependencies (including heavy PDF/DOCX parsers)
#   make run       - Run a quick test scrape
#   make discover  - Web-search + ingest (feed the crawl from a live query)
#   make test      - Hybrid-search smoke test
#   make clean     - Remove cached data, vault, and Python cache
#
# Set PYTHON to override the interpreter (defaults to python3.11).

.PHONY: install run discover test clean

PYTHON ?= python3.11

install:
	@echo "🔧 Installing HoardCore core deps into a virtualenv..."
	@test -d venv || $(PYTHON) -m venv venv
	venv/bin/python -m pip install --upgrade pip
	venv/bin/python -m pip install aiohttp curl_cffi trafilatura readability-lxml tomli PyMuPDF python-docx ebooklib lxml
	@echo "For OCR of scanned PDFs, install the optional fallback next:"
	@echo "  venv/bin/python -m pip install rapidocr_onnxruntime"
	@echo "✅ Lightweight install complete (venv)."

run:
	@echo "🚀 Testing HoardCore on a sample URL..."
	venv/bin/python hoardcore.py https://example.com --action scrape

discover:
	@echo "🔎 Searching the web and ingesting top results..."
	venv/bin/python hoardcore.py "_" --action discover --query "nothing here -- pass a query like 'negros renewable energy'"

test:
	@echo "🧪 Running HCH test suite (pytest)..."
	venv/bin/python -m pytest tests/ -q

clean:
	@echo "🧹 Cleaning up HoardCore data..."
	rm -rf hoardcore_data/
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	rm -f hoardcore.toml
	@echo "✅ Clean complete."