"""The repository's own quality gates are pinned, not aspirational.

A green PR must mean the workflow actually ran the suites: this test fails if
the CI stub regresses to a no-op, or if the documented pre-PR commands drift
away from the scripts they are supposed to run.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_runs_lint_python_tests_and_both_browser_suites():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check --select E9,F63,F7,F82" in workflow
    assert "pytest tests/" in workflow
    assert "browser_review_page.mjs" in workflow
    assert "browser_review_portal.mjs" in workflow
    assert "google-chrome" in workflow
    assert "upload-artifact" in workflow
    assert "echo ok" not in workflow.lower()


def test_pre_commit_runs_the_review_gate_and_the_harness():
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "/ruff-pre-commit" in config
    assert "review-interface-tests" in config
    assert "portal_browser_harness.py" in config


def test_make_targets_exist_for_the_documented_gate():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("check:", "check-all:", "browser-real:", "test-review:", "harness:"):
        assert target in makefile
    testing = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
    for command in ("make check", "make check-all", "make browser", "make axe"):
        assert command in testing


def test_pr_template_asks_for_the_gates_and_the_safety_limits():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    for phrase in ("make check", "make check-all", "credential", "short display name"):
        assert phrase in template
