# Maintainer commands. `make check` is the pre-PR gate; `make check-all` adds
# the real-browser suites (Chrome + axe-core).
PY ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
CHROME ?= $(shell command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
AXE_PATH ?= .cache/axe/node_modules/axe-core/axe.min.js
EVIDENCE ?= $(CURDIR)/.cache/browser-evidence
export PYTHONPATH := src

.PHONY: help install lint test test-review check check-all browser browser-synthetic browser-real axe harness clean

help:
	@echo "make install          create .venv and install test/lint/browser dependencies"
	@echo "make lint             syntax + undefined-name gate (fast)"
	@echo "make test             full offline pytest suite"
	@echo "make test-review      focused review-interface tests"
	@echo "make harness          build the browser harness packet and print its summary"
	@echo "make check            lint + harness + full tests (the pre-PR gate)"
	@echo "make check-all        check + both browser suites (needs Chrome)"
	@echo "make browser          both browser suites only"
	@echo "make axe              install axe-core under .cache/axe"
	@echo "make clean            remove .cache/browser-evidence and the local harness root"

install:
	@test -x "$(PY)" || uv venv --python 3.13 .venv
	uv pip install --python "$(PY)" -r requirements.txt pytest openpyxl ruff pre-commit

lint:
	"$(RUFF)" check --select E9,F63,F7,F82 src tests scripts run_all.py
	"$(PY)" -m compileall -q src scripts tests

test:
	"$(PY)" -m pytest tests/ -q

test-review:
	"$(PY)" -m pytest tests/test_labeling_review_page.py tests/test_labeling_review_portal.py \
		tests/test_labeling_review_packet.py tests/test_labeling_review_export.py \
		tests/test_labeling_review_corporate.py tests/test_labeling_review_production.py \
		tests/test_review_checkpoint_recovery.py -q

harness:
	"$(PY)" tests/portal_browser_harness.py --root "$(CURDIR)/.cache/browser-harness" --check

check: lint harness test

check-all: check axe browser

browser: browser-synthetic browser-real

browser-synthetic:
	mkdir -p "$(EVIDENCE)/synthetic"
	CHROME="$(CHROME)" AXE_PATH="$(AXE_PATH)" node tests/browser_review_page.mjs "$(EVIDENCE)/synthetic"

browser-real:
	mkdir -p "$(EVIDENCE)/real"
	CHROME="$(CHROME)" AXE_PATH="$(AXE_PATH)" PYTHON="$(PY)" \
		node tests/browser_review_portal.mjs "$(EVIDENCE)/real"

axe:
	mkdir -p .cache/axe
	npm install --prefix .cache/axe --no-save axe-core

clean:
	rm -rf "$(EVIDENCE)" .cache/browser-harness .browser-portal
