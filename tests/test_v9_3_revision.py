"""Unit tests for mailroom_eda.v9_3_revision (corporate presence layer).

The transform/verify/render functions run network-free on synthetic rows.
The end-to-end staging build test runs against the local snapshots when
``data/v9_3/{base_snapshot,branch_snapshot}`` exist (they ship pins checked
against the Hub on first build) and skips otherwise."""
from __future__ import annotations

import json

import pytest

from mailroom_eda import gt_presence as gp
from mailroom_eda import v9_1_revision as v91
from mailroom_eda import v9_3_revision as v93
from mailroom_eda.gt_presence import ALL_GT_FIELDS, BASE_GT_FIELDS


def _gt_presence(**over):
    m = {k: "not_applicable" for k in BASE_GT_FIELDS}
    m.update(over)
    return json.dumps(m)


def _corp_row(filename: str = "corp_001.htm", **over) -> dict:
    row = {
        "filename": filename,
        "expected": "corporate_record",
        "expected_subclass": "bylaws",
        "gt_fields": {
            "intent": "governance_rules",
            "subject_matter": "bylaws of ACME CORP (CIK 0001234567) (SEC EDGAR exhibit).",
            "keywords": "[]",
            "intent_source": "heuristic",
            "gt_presence": _gt_presence(record_type="populated", filing_number="not_applicable"),
        },
        "doc_text": "",
        "metadata": {"source_dataset": ""},
        "document_id": "DOC-x",
        "content_sha256": "hash",
    }
    row.update(over)
    return row


def _other_row(filename: str = "ins_001.txt") -> dict:
    return {
        "filename": filename,
        "expected": "insurance_claim",
        "expected_subclass": "auto",
        "gt_fields": {
            "claim_number": "X-1", "person": "",
            "gt_presence": _gt_presence(claim_number="populated"),
        },
        "metadata": {},
        "document_id": "DOC-y",
        "content_sha256": "hash2",
        "doc_text": "",
    }


class TestRelabelAllRows:
    def test_corporate_rows_relabel_others_pass_through(self):
        old = [_corp_row(), _other_row()]
        new = v93.relabel_all_rows(old)
        assert len(new) == 2
        # corporate: presence extended, no invented facts
        presence = json.loads(new[0]["gt_fields"]["gt_presence"])
        assert presence["entity_name"] == "populated"
        assert presence["record_type"] == "populated"
        # non-corporate: byte-identical pass-through
        assert json.dumps(new[1], sort_keys=True) == json.dumps(old[1], sort_keys=True)

    def test_guard_stops_on_attempted_non_corporate_drift(self, monkeypatch):
        def bad(rows):
            for r in rows:
                out = dict(r)
                if out["expected"] != "corporate_record":
                    out["content_sha256"] = "TAMPERED"
                yield out, []
        monkeypatch.setattr(v93, "relabel_rows", bad)
        with pytest.raises(AssertionError, match="non-corporate row"):
            v93.relabel_all_rows([_other_row()])


class TestVerifyPresenceLaws:  # (b) + (c)
    def test_corporate_extension_passes(self):
        rows = v93.relabel_all_rows([_corp_row()])
        stats = v93.verify_presence_laws(rows)
        assert stats["corporate_rows"] == 1
        assert set(stats["corporate_codes"]) <= set(gp.PRESENCE_CODES)

    def test_unknown_key_rejected(self):
        row = _corp_row()
        row["gt_fields"]["gt_presence"] = _gt_presence(record_type="populated", filing_number="not_applicable")
        m = json.loads(row["gt_fields"]["gt_presence"])
        m["extraneous_field"] = "populated"
        row["gt_fields"]["gt_presence"] = json.dumps(m)
        with pytest.raises(AssertionError, match="outside ALL_GT_FIELDS"):
            v93.verify_presence_laws([row])

    def test_base_key_dropped_rejected(self):
        row = _corp_row()
        m = json.loads(row["gt_fields"]["gt_presence"])
        del m["intent"]
        row["gt_fields"]["gt_presence"] = json.dumps(m)
        with pytest.raises(AssertionError, match="base presence keys missing"):
            v93.verify_presence_laws([row])

    def test_unlabeled_sentinel_never_ships(self):
        row = _corp_row()
        m = json.loads(row["gt_fields"]["gt_presence"])
        m["trustee"] = "unlabeled"
        row["gt_fields"]["gt_presence"] = json.dumps(m)
        with pytest.raises(AssertionError, match="outside the published vocabulary"):
            v93.verify_presence_laws([row])

    def test_non_corporate_extension_rejected(self):
        row = _other_row()
        m = json.loads(row["gt_fields"]["gt_presence"])
        m["trustee"] = "populated"
        row["gt_fields"]["gt_presence"] = json.dumps(m)
        with pytest.raises(AssertionError, match="entity presence keys"):
            v93.verify_presence_laws([row])


class TestSubclassConditionalLaw:
    """v9.3 hardening: a corporate row's present extension keys ⊆ entity
    core + its OWN subclass's extension keys (registry mirror, sha-pinned);
    foreign-subclass extensions are ABSENT; unknown subclass ⇒ core-only."""

    def test_bylaws_row_with_foreign_indenture_extension_fails(self):
        row = _corp_row(
            gt_fields={"gt_presence": _gt_presence(
                record_type="populated", filing_number="not_applicable", trustee="populated"
            )}
        )
        assert row["expected_subclass"] == "bylaws"  # trustee is an indenture extension
        with pytest.raises(AssertionError, match="foreign-subclass"):
            v93.verify_presence_laws([row])

    def test_bylaws_row_with_own_extension_passes(self):
        row = _corp_row(
            gt_fields={"gt_presence": _gt_presence(
                record_type="populated", filing_number="not_applicable",
                certifying_officer="populated",  # a bylaws extension key
            )}
        )
        stats = v93.verify_presence_laws([row])
        assert stats["corporate_rows"] == 1

    def test_unknown_subclass_row_with_extension_fails(self):
        # Registry-driven: an unknown expected_subclass is treated core-only.
        row = _corp_row(
            expected_subclass="no_such_subclass",
            gt_fields={"gt_presence": _gt_presence(
                record_type="populated", filing_number="not_applicable", trustee="populated"
            )}
        )
        with pytest.raises(AssertionError, match="foreign-subclass"):
            v93.verify_presence_laws([row])

    def test_unknown_subclass_row_with_core_keys_passes(self):
        row = _corp_row(
            expected_subclass="no_such_subclass",
            gt_fields={"gt_presence": _gt_presence(
                record_type="populated", filing_number="not_applicable",
                signatories="populated",  # entity-CORE key: always allowed
            )}
        )
        stats = v93.verify_presence_laws([row])
        assert stats["corporate_rows"] == 1

    def test_single_sourced_with_audit_path(self):
        # gt_quality.audit_row must reach the same verdict as the staging
        # verifier for the same map (the law is single-sourced in
        # gt_presence.presence_law_problems, never re-declared).
        from mailroom_eda.gt_presence import presence_law_problems

        row = _corp_row()
        presence = json.loads(row["gt_fields"]["gt_presence"])
        presence["trustee"] = "populated"
        problems = presence_law_problems(presence, "corporate_record", "bylaws")
        assert problems and "foreign-subclass" in problems[0]
        row["gt_fields"]["gt_presence"] = json.dumps(presence)
        with pytest.raises(AssertionError, match="foreign-subclass"):
            v93.verify_presence_laws([row])


class TestVerifyZeroDrift:
    def test_relabeled_corporate_changes_allowed(self):
        old = [_corp_row(), _other_row()]
        new = v93.relabel_all_rows(old)
        v93.verify_zero_drift(old, new)  # must not raise

    def test_non_corporate_tamper_rejected(self):
        old = [_other_row()]
        new = v93.relabel_all_rows(old)
        new[0]["doc_text"] = "TAMPERED"
        with pytest.raises(AssertionError, match="drifted"):
            v93.verify_zero_drift(old, new)

    def test_corporate_frozen_gt_keys_rejected(self):
        old = [_corp_row()]
        old[0]["gt_fields"]["cuad_clause_labels"] = ""  # frozen, non-relabel key
        new = v93.relabel_all_rows(old)
        new[0]["gt_fields"]["cuad_clause_labels"] = "TAMPERED"
        with pytest.raises(AssertionError, match="altered by the relabel pass"):
            v93.verify_zero_drift(old, new)


class TestVerifyLabelsEqualBranch:
    def test_faithful_rows_pass(self, monkeypatch, tmp_path):
        branch_rows = v93.relabel_all_rows([_corp_row(), _other_row()])
        new_rows = v93.relabel_all_rows([_corp_row(), _other_row()])
        stats = v93.verify_labels_equal_branch(new_rows, branch_rows)
        assert stats == {"corporate": 1, "non_corporate": 1}

    def test_corporate_divergence_reported(self):
        branch_rows = v93.relabel_all_rows([_corp_row()])
        new_rows = v93.relabel_all_rows([_corp_row()])
        new_rows[0]["gt_fields"]["jurisdiction"] = "Delaware"  # an invented fact
        with pytest.raises(AssertionError, match="diverges from the v9.2.1 source"):
            v93.verify_labels_equal_branch(new_rows, branch_rows)

    def test_non_corporate_divergence_reported(self):
        branch_rows = v93.relabel_all_rows([_other_row()])
        new_rows = v93.relabel_all_rows([_other_row()])
        new_rows[0]["gt_fields"]["claim_number"] = "TAMPERED"
        with pytest.raises(AssertionError, match="non-corporate row diverges"):
            v93.verify_labels_equal_branch(new_rows, branch_rows)


class TestVerifyRawLineEquality:
    def test_deterministic_rebuild_is_byte_identical(self, tmp_path):
        src = tmp_path / "branch.jsonl"
        rows = v93.relabel_all_rows([_corp_row(), _other_row()])
        lines = [
            json.dumps(r, default=str, ensure_ascii=False).encode() for r in rows
        ]
        src.write_bytes(b"\n".join(lines) + b"\n")
        rebuilt = v93.relabel_all_rows([_corp_row(), _other_row()])
        stats = v93.verify_raw_line_equality(rebuilt, src)
        assert stats == {"corporate": 1, "non_corporate": 1}

    def test_byte_divergence_raises_stop(self, tmp_path):
        src = tmp_path / "branch.jsonl"
        rows = v93.relabel_all_rows([_corp_row()])
        src.write_text(json.dumps(rows[0], default=str, ensure_ascii=False) + "\n")
        rebuilt = v93.relabel_all_rows([dict(_corp_row(), content_sha256="other")])
        with pytest.raises(AssertionError, match="STOP"):
            v93.verify_raw_line_equality(rebuilt, src)


class TestManifestAndCard:
    OLD_MANIFEST = (
        "name       : Lucius-Morningstar/mailroom-dataset\n"
        "version    : v1 (canonically v9 of the mailroom corpus family)\n"
        "rows       : 3302 (train 2979, test 323)\n"
        "split_rule : md5(filename) % 10 == 0 -> test (evaluation partition, NOT ML semantics)\n"
        "built_utc  : 2026-10-06T05:56:49Z\n"
        "revision   : v9.2.1 corporate_record label repair (old text)\n"
        "sha256     :\n"
        "  README.md  " + "a" * 64 + "\n"
    )

    def test_dataset_version_line_untouched(self):
        out = v93.render_manifest_v9_3(self.OLD_MANIFEST, {}, "2026-10-17T12:00:00Z")
        assert "version    : v1 (canonically v9 of the mailroom corpus family)" in out

    def test_labels_version_line_added_once(self):
        out = v93.render_manifest_v9_3(self.OLD_MANIFEST, {}, "2026-10-17T12:00:00Z")
        assert out.count("labels_version") == 1
        assert v93.LABELS_VERSION in out

    def test_revision_line_replaced(self):
        out = v93.render_manifest_v9_3(self.OLD_MANIFEST, {}, "2026-10-17T12:00:00Z")
        assert out.count("revision   :") == 1
        assert "v9.3 corporate presence-layer revision" in out
        assert "old text" not in out

    def test_old_labels_version_line_replaced_on_reapply(self):
        once = v93.render_manifest_v9_3(self.OLD_MANIFEST, {}, "2026-10-17T12:00:00Z")
        twice = v93.render_manifest_v9_3(once, {}, "2026-10-18T12:00:00Z")
        assert twice.count("labels_version") == 1
        assert twice.count("built_utc  :") == 1

    def test_sha_table_merges(self):
        new_sha = "d" * 64
        out = v93.render_manifest_v9_3(
            self.OLD_MANIFEST, {"README.md": new_sha}, "2026-10-17T12:00:00Z"
        )
        assert f"  README.md  {new_sha}" in out

    def test_addendum_documents_vocabulary_verbatim(self):
        addendum = v93.render_readme_addendum(3302, {"populated": 2212, "not_applicable": 8070})
        # the four published codes, verbatim, in dojo order
        for code in ("populated", "not_applicable", "schema_documented_absence", "pending_annotation"):
            assert f"`{code}`" in addendum
        # the fifth, scorer-only unstated-blank regime is named
        assert "unstated-blank" in addendum and "unlabeled" in addendum
        assert addendum.startswith("## v9.3 corporate presence-layer labels (2026-10-17)")
        assert addendum.endswith("\n\n")


class TestPublishGate:
    def test_staging_run_refuses_publish(self, tmp_path):
        with pytest.raises(SystemExit, match="STAGING"):
            v93.build_all(tmp_path, publish=True)


class TestRowSetLaw:
    def test_row_set_report(self):
        rows = [_corp_row(filename=f"f{i}.htm") for i in range(450)]
        rows += [_other_row(filename=f"g{i}.txt") for i in range(2852)]
        for i, r in enumerate(rows[:2979]):
            r["split"] = "train"
        for r in rows[2979:]:
            r["split"] = "test"
        stats = v93.verify_row_set(rows)
        assert stats["rows"] == 3302 and stats["corporate"] == 450

    def test_row_set_drift_rejected(self):
        rows = [_corp_row(filename="a.htm")]  # wrong counts
        with pytest.raises(AssertionError, match="row set drifted"):
            v93.verify_row_set(rows)


class TestRegistryPins:
    def test_expected_counts(self):
        assert v93.EXPECTED_CORPORATE_ROWS == 450
        assert v93.EXPECTED_CORPORATE_ENTITY_KEYS == 37
        assert v93.EXPECTED_TOTAL_ROWS == 3302
        assert v93.EXPECTED_SPLIT_COUNTS == {"train": 2979, "test": 323}

    def test_versus_v9_1_relaxed_law(self):
        # the v9.3 law is a superset relation: ALL_GT_FIELDS is the registry
        # ceiling, BASE_GT_FIELDS the required floor — the v9.2 era's exact
        # equality law (an assertion in verify_presence_codes) is the policy
        # this revision relaxes, keeping the qualification corp-only.
        stats = v93.verify_presence_laws(v93.relabel_all_rows([_corp_row()]))
        assert stats["all_gt_fields"] > stats["base_gt_fields"]


# ---------------------------------------------------------------------------
# end-to-end staging build (data-dependent)
# ---------------------------------------------------------------------------

BASE_SIDECAR = v93.BASE_SNAPSHOT_DIR / "ground_truth_hardened.jsonl"
BRANCH_SIDECAR = v93.BRANCH_SNAPSHOT_DIR / "ground_truth_hardened.jsonl"


@pytest.mark.skipif(
    not (BASE_SIDECAR.is_file() and BRANCH_SIDECAR.is_file()),
    reason="local v9.2 base + corporate-labels-v9.2.1 branch snapshots absent (gitignored data/)",
)
class TestStagingBuildAll:
    def test_full_staging_build_gates(self, tmp_path):
        result = v93.build_all(tmp_path / "stage", base_path=str(BASE_SIDECAR))
        assert result["status"] == "staged"
        assert result["row_set"] == {
            "rows": 3302, "splits": {"train": 2979, "test": 323}, "corporate": 450,
        }
        assert result["labels_stats"]["corporate"] == 450
        assert result["staged_parquet_stats"] == {
            "rows": 3302, "splits": {"train": 2979, "test": 323}, "corporate": 450,
        }
        stage = tmp_path / "stage"
        # the staged label surfaces are byte-pinned to the verified source
        import hashlib

        sha = hashlib.sha256((stage / "ground_truth_hardened.jsonl").read_bytes()).hexdigest()
        assert sha == v93.BRANCH_SIDECAR_SHA256
        manifest = (stage / "manifest.txt").read_text(encoding="utf-8")
        assert "labels_version:" in manifest
        assert "v9.3 corporate presence-layer revision" in manifest
        # README addendum applied, once
        card = (stage / "README.md").read_text(encoding="utf-8")
        assert card.count("## v9.3 corporate presence-layer labels (2026-10-17)") == 1

    def test_build_is_deterministic(self, tmp_path):
        v93.build_all(tmp_path / "a", base_path=str(BASE_SIDECAR))
        v93.build_all(tmp_path / "b", base_path=str(BASE_SIDECAR))

        def _files(root):
            return {
                p.relative_to(root) for p in root.rglob("*") if p.is_file()
            }

        a_files, b_files = _files(tmp_path / "a"), _files(tmp_path / "b")
        assert a_files == b_files
        for rel in sorted(a_files):
            if rel.name == "manifest.txt":
                continue
            assert (tmp_path / "a" / rel).read_bytes() == (
                tmp_path / "b" / rel
            ).read_bytes(), str(rel)


# ---------------------------------------------------------------------------
# gate (g): staged parquet ⟷ hardened JSONL gt_fields (synthetic pair)
# ---------------------------------------------------------------------------


class TestStagedParquetGate:
    """Cheap synthetic-pair unit test for the passthrough content gate: the
    byte-copied ``parquet/ground_truth`` must carry exactly the JSONL rows'
    ``gt_fields`` (row counts + split counts + join on filename + string
    equality field-by-field)."""

    @staticmethod
    def _rows() -> list[dict]:
        corr = {
            "filename": "corr_001.txt", "expected": "correspondence",
            "expected_subclass": "email", "split": "train",
            "gt_fields": {"intent": "request", "gt_presence": json.dumps(
                {"intent": "populated", "keywords": "populated"})},
        }
        ins = {
            "filename": "ins_001.txt", "expected": "insurance_claim",
            "expected_subclass": "auto", "split": "test",
            "gt_fields": {"claim_number": "X-1", "gt_presence": json.dumps(
                {"claim_number": "populated", "adjuster": "not_applicable"})},
        }
        bylaws = {
            "filename": "corp_001.htm", "expected": "corporate_record",
            "expected_subclass": "bylaws", "split": "train",
            "gt_fields": {
                "intent": "governance_rules", "record_type": "bylaws",
                "entity_name": "ACME Corp",
                "gt_presence": json.dumps({
                    **{k: "not_applicable" for k in BASE_GT_FIELDS},
                    "intent": "populated", "record_type": "populated",
                    "entity_name": "populated", "filing_number": "not_applicable",
                }),
            },
        }
        indenture = {
            "filename": "corp_002.htm", "expected": "corporate_record",
            "expected_subclass": "indenture", "split": "test",
            "gt_fields": {
                "intent": "investor_rights", "record_type": "other",
                "gt_presence": json.dumps({
                    **{k: "not_applicable" for k in BASE_GT_FIELDS},
                    "intent": "populated", "trustee": "populated",
                    "record_type": "populated", "jurisdiction": "not_applicable",
                }),
            },
        }
        return [corr, ins, bylaws, indenture]

    @staticmethod
    def _stage_parquet(rows: list[dict], stage_dir) -> None:
        """The staging parquet convention: gt_fields is one JSON-string
        column (key order preserved from the writer — the gate parses, it
        does not assume sorted keys)."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        for split in ("train", "test"):
            part = [dict(r, gt_fields=json.dumps(r["gt_fields"], ensure_ascii=False))
                    for r in rows if r["split"] == split]
            gdir = stage_dir / "parquet" / "ground_truth" / split
            gdir.mkdir(parents=True, exist_ok=True)
            pq.write_table(
                pa.Table.from_pylist(part),
                gdir / f"{split}-00000-of-00001.parquet",
            )

    def test_synthetic_pair_matches(self, tmp_path):
        rows = self._rows()
        self._stage_parquet(rows, tmp_path)
        # split counts differ from the 2979/323 corpus law on the synthetic
        # pair — the gate's counts are TOTAL vs the passed rows; expect PASS.
        stats = v93.verify_staged_parquet_gt_fields(rows, tmp_path)
        assert stats["rows"] == 4
        assert stats["splits"] == {"train": 2, "test": 2}
        assert stats["corporate"] == 2

    def test_tampered_field_value_fails(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = self._rows()
        self._stage_parquet(rows, tmp_path)
        # tamper: re-write the staged train parquet with one diverged
        # gt_fields value on the corporate row (a proper pyarrow rewrite —
        # no byte surgery).
        path = tmp_path / "parquet" / "ground_truth" / "train" / "train-00000-of-00001.parquet"
        recs = pq.read_table(path).to_pylist()
        for rec in recs:
            if rec["filename"] == "corp_001.htm":
                rec["gt_fields"] = rec["gt_fields"].replace("ACME Corp", "Other Corp", 1)
                assert "Other Corp" in rec["gt_fields"]
        pq.write_table(
            pa.Table.from_pylist(recs, schema=pq.read_schema(path)), path
        )
        with pytest.raises(AssertionError, match="gt_fields"):
            v93.verify_staged_parquet_gt_fields(rows, tmp_path)

    def test_missing_jsonl_counterpart_fails(self, tmp_path):
        rows = self._rows()
        self._stage_parquet(rows, tmp_path)
        pruned = [r for r in rows if r["filename"] != "corp_002.htm"]
        with pytest.raises(AssertionError, match="no hardened JSONL counterpart"):
            v93.verify_staged_parquet_gt_fields(pruned, tmp_path)

    def test_row_count_drift_fails(self, tmp_path):
        rows = self._rows()
        self._stage_parquet(rows, tmp_path)
        # an extra JSONL row with no parquet counterpart: every parquet row
        # still joins, so only the count-drift assert catches it
        extra = dict(rows[0], filename="extra.txt")
        with pytest.raises(AssertionError, match="row count drift"):
            v93.verify_staged_parquet_gt_fields([*rows, extra], tmp_path)
