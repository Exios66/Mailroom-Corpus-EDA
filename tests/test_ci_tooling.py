"""The repository's quality gates are local, repeatable, and not GitHub Actions.

The project runs on a host account where Actions are unavailable (and the
repository standard is now local gates). These tests fail if a workflow is
added back, if the documented commands drift, or if the coverage tool that
replaces CI reporting disappears.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_github_actions_workflows_are_committed():
    workflows = ROOT / ".github" / "workflows"
    files = sorted(workflows.glob("*.y*ml")) if workflows.exists() else []
    assert files == [], f"GitHub Actions workflows are not used here: {files}"


def test_make_targets_exist_for_the_documented_local_gate():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("check:", "check-all:", "browser-real:", "test-review:", "harness:",
                   "coverage:", "coverage-check:"):
        assert target in makefile
    testing = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
    for command in ("make check", "make check-all", "make browser", "make axe",
                    "make coverage", "make coverage-check"):
        assert command in testing


def test_pre_commit_runs_the_review_gate_and_the_pre_push_gate():
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "/ruff-pre-commit" in config
    assert "review-interface-tests" in config
    assert "portal_browser_harness.py" in config
    assert "pre-push" in config
    assert "make check" in config


def test_coverage_tool_replaces_the_unavailable_ci_report():
    tool = ROOT / "scripts" / "ci" / "coverage_report.py"
    text = tool.read_text(encoding="utf-8")
    assert "coverage.Coverage" in text
    assert "--fail-under" in text
    assert "PREIMPORT" in text  # the Python 3.13/numpy tracer workaround
    assert "coverage" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_pr_template_asks_for_the_gates_and_the_safety_limits():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    for phrase in ("make check", "make check-all", "credential", "short display name"):
        assert phrase in template
