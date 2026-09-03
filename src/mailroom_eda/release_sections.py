"""Shared constants + card helpers for the §84 hardened-release scripts
(publish_hardened / reconcile_gt_v8 — HUB-022, HUB-032).

Kept in src so the release tooling has exactly one copy of the column
registries and the card-section anchors; the scripts stay thin CLIs.
"""
from __future__ import annotations

IDENTITY_FIELDS = (
    "document_id", "source_corpus", "source_document_id", "source_filename",
    "source_revision", "content_sha256", "normalized_text_sha256",
)
CONTRACT_FIELDS = (
    "expected_specialist", "expected_stage", "review_expected", "review_reason",
    "retry_expected", "expected_post_retry_state",
    "annotation_source", "annotation_method", "annotation_model",
    "annotation_prompt_version", "annotation_confidence", "annotation_reviewer",
    "annotation_timestamp",
)
MATTER_SCALARS = (
    "matter_id", "matter_construction", "group_id", "group_role",
    "thread_position", "thread_size", "thread_evidence",
)
MATTER_LISTS = ("relationships", "related_document_ids")

#: The live card section owned by the §84 hardened release (HUB-022/HUB-032).
#: `upsert_section` replaces it in place; the anchor heading below must exist
#: exactly once on the live card. The legacy heading is the pre-reconciliation
#: §84 section title still present on the Hub (HUB-032 renames it).
CARD_HEADING = "## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02; reconciled onto the v8 base, HUB-032)"
CARD_HEADING_LEGACY = "## Mailroom evaluation hardening (v0.2/v0.3/v0.4, 2026-09-02)"
CARD_ANCHOR = "## Original files (KANBAN-105 addendum, 2026-08-30)"


def replace_card_section(card: str, new_body: str) -> str:
    """Replace the §84 section (legacy or reconciled title) with ``new_body``.

    ``new_body`` must start with ``CARD_HEADING`` and end with a blank line
    (the `upsert_section` body contract). Raises when the live card does not
    carry exactly one of the two owned titles.
    """
    from .docclass_uploader import upsert_section

    if card.count(CARD_HEADING) == 1:
        return upsert_section(card, CARD_HEADING, new_body, CARD_ANCHOR)
    legacy_n = card.count(CARD_HEADING_LEGACY)
    assert legacy_n == 1, (
        f"§84 section not found on live card "
        f"(legacy x{legacy_n}, reconciled x{card.count(CARD_HEADING)})"
    )
    start = card.find(CARD_HEADING_LEGACY)
    nxt = card.find("\n## ", start + 1)
    end = len(card) if nxt < 0 else nxt + 1
    card = card[:start] + card[end:]
    assert card.count(CARD_ANCHOR) == 1, "card anchor not unique after section removal"
    return card[:card.find(CARD_ANCHOR)] + new_body + card[card.find(CARD_ANCHOR):]


def fetch_live_card(repo_id: str) -> str | None:
    """The current dataset card (None when offline / no token)."""
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo_id, filename="README.md", repo_type="dataset")
        from pathlib import Path

        return Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        print(f"WARN: could not fetch live card ({exc})")
        return None
