# Local maintainer gates. GitHub Actions are intentionally not used; these
# commands are the CI substitute. `make check` is the pre-PR gate,
# `make check-all` adds the real-browser suites, `make coverage` reports real
# line/branch coverage from the unit tests.
PY ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
CHROME ?= $(shell command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
AXE_PATH ?= .cache/axe/node_modules/axe-core/axe.min.js
EVIDENCE ?= $(CURDIR)/.cache/browser-evidence
COVERAGE_MIN ?= 70
COVERAGE_OUT ?= .cache/quality/coverage.json
export PYTHONPATH := src

.PHONY: help install lint test test-review check check-all browser browser-synthetic browser-real axe harness coverage coverage-review coverage-check clean

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
	@echo "make coverage         full unit-test coverage report (JSON + table)"
	@echo "make coverage-review  coverage for the focused review-interface tests"
	@echo "make coverage-check   coverage + fail below COVERAGE_MIN (default 70)"
	@echo "make clean            remove local evidence, coverage and harness output"

install:
	@test -x "$(PY)" || uv venv --python 3.13 .venv
	uv pip install --python "$(PY)" -r requirements.txt pytest openpyxl ruff pre-commit "coverage==7.17.2"
	@if [ -d .git ]; then "$(PY)" -m pre_commit install && "$(PY)" -m pre_commit install --hook-type pre-push; fi

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

coverage:
	"$(PY)" scripts/ci/coverage_report.py --tests tests/ --out "$(COVERAGE_OUT)"

coverage-review:
	"$(PY)" scripts/ci/coverage_report.py --tests tests/test_labeling_review_page.py \
		tests/test_labeling_review_portal.py tests/test_labeling_review_export.py \
		tests/test_labeling_review_corporate.py tests/test_labeling_review_production.py \
		tests/test_review_checkpoint_recovery.py --out .cache/quality/coverage-review.json

coverage-check:
	"$(PY)" scripts/ci/coverage_report.py --tests tests/ --out "$(COVERAGE_OUT)" \
		--fail-under "$(COVERAGE_MIN)"

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
	rm -rf "$(EVIDENCE)" .cache/browser-harness .cache/quality .browser-portal
