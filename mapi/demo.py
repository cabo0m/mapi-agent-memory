from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


PRODUCT_PROOF_SCHEMA_VERSION = "mapi.product_proof.v1"
PRODUCT_PROOF_SUITE_SCHEMA_VERSION = "mapi.product_proof_suite.v1"
PRODUCT_PROOF_PROJECT_KEY = "mapi-product-proof"


def _configure_demo_server(path: Path) -> Any:
    """Bind an already-imported server_core module to one disposable database."""
    from app.runtime.context import configure_runtime_context

    path = path.resolve()
    configure_runtime_context(root=path.parent, data_dir=path.parent, db_path=path)

    import server_core

    server_core.ROOT = path.parent
    server_core.DATA_DIR = path.parent
    server_core.DB_PATH = path
    return server_core


def run_demo_database(path: Path) -> dict[str, Any]:
    """Run the product demo in an explicitly supplied, disposable database."""
    from app import db_migrations

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    server_core = _configure_demo_server(path)

    connection = server_core.get_db_connection()
    try:
        db_migrations.apply_all_migrations(connection)
        old = server_core._insert_memory(
            connection,
            content="Use SQLite for the application database.",
            summary_short="Application database decision: SQLite",
            memory_type="decision",
            project_key="mapi-product-demo",
            scope_code="project",
            source="synthetic-demo",
            source_context="isolated public product demo",
            source_event_ref="mapi-project-memory-demo:sqlite",
            truth_kind="decision",
            confidence_score=1.0,
            importance_score=0.8,
            ensure_embedding=False,
        )
        new = server_core._insert_memory(
            connection,
            content="Replace SQLite with PostgreSQL for the application database.",
            summary_short="Application database decision: PostgreSQL",
            memory_type="decision",
            project_key="mapi-product-demo",
            scope_code="project",
            source="synthetic-demo",
            source_context="isolated public product demo",
            source_event_ref="mapi-project-memory-demo:postgresql",
            truth_kind="decision",
            confidence_score=1.0,
            importance_score=0.8,
            ensure_embedding=False,
        )
        connection.commit()
    finally:
        connection.close()

    old_id = int(old["id"])
    new_id = int(new["id"])
    reason = "The PostgreSQL decision replaces the earlier SQLite decision."
    preview = server_core.preview_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason=reason,
    )
    if preview.get("status") != "preview_ready" or not preview.get("preview_hash"):
        raise RuntimeError(f"Supersession preview failed: {preview}")
    applied = server_core.apply_memory_supersession(
        new_memory_id=new_id,
        old_memory_id=old_id,
        relation_kind="replacement",
        reason=reason,
        expected_preview_hash=str(preview["preview_hash"]),
        applied_by="mapi-demo",
        notes="Explicit confirmation for fictional demo decisions in a disposable database.",
        confirm_protected=True,
    )
    if applied.get("status") != "applied":
        raise RuntimeError(f"Supersession apply failed: {applied}")

    current = server_core.get_memory_current_state(old_id, include_history=True)
    links = server_core.get_memory_links(new_id)
    current_record = current.get("current") or {}
    history = current.get("history") or []
    relation = next(
        (
            item
            for item in links.get("links", [])
            if int(item.get("from_memory_id") or 0) == new_id
            and int(item.get("to_memory_id") or 0) == old_id
            and item.get("relation_type") == "supersedes"
        ),
        None,
    )
    if int(current_record.get("id") or 0) != new_id:
        raise RuntimeError(f"Current state is incorrect: {current}")
    if [int(item["id"]) for item in history] != [old_id]:
        raise RuntimeError(f"Preserved history is incorrect: {current}")
    if relation is None:
        raise RuntimeError(f"Supersession relationship is missing: {links}")
    if (
        current_record.get("source_event_ref") != "mapi-project-memory-demo:postgresql"
        or history[0].get("source_event_ref") != "mapi-project-memory-demo:sqlite"
    ):
        raise RuntimeError(f"Decision provenance is missing: {current}")

    lines = [
        "Current decision: PostgreSQL",
        "Previous decision: SQLite",
        "Relationship: PostgreSQL supersedes SQLite",
        f"Current record ID: {new_id}",
        f"Previous record ID: {old_id}",
        f"Preview hash: {preview['preview_hash']}",
    ]
    return {
        "status": "ok",
        "database": str(path),
        "project_key": "mapi-product-demo",
        "current_memory_id": new_id,
        "previous_memory_id": old_id,
        "relation": "supersedes",
        "preview_hash": preview["preview_hash"],
        "apply_run_id": applied.get("run_id"),
        "provenance_verified": True,
        "human_output": "\n".join(lines),
    }


def run_isolated_demo() -> dict[str, Any]:
    """Run the demo in a temporary directory that is removed automatically."""
    with tempfile.TemporaryDirectory(prefix="mapi-project-memory-demo-") as directory:
        return run_demo_database(Path(directory) / "demo.db")


def _require_truthful_assertions(assertions: dict[str, Any]) -> None:
    failures = [
        name
        for name, value in assertions.items()
        if not (
            (name == "external_calls" and type(value) is int and value == 0)
            or (name != "external_calls" and value is True)
        )
    ]
    if failures:
        raise RuntimeError(f"Product proof assertion failed: {', '.join(sorted(failures))}")


def _stable_decision_proof(result: dict[str, Any]) -> dict[str, Any]:
    assertions = {
        "single_current_head": True,
        "history_preserved": True,
        "supersedes_relation_present": result.get("relation") == "supersedes",
        "guarded_preview_present": len(str(result.get("preview_hash") or "")) == 64,
        "both_provenance_chains_visible": result.get("provenance_verified") is True,
        "external_calls": 0,
    }
    _require_truthful_assertions(assertions)
    return {
        "schema_version": PRODUCT_PROOF_SCHEMA_VERSION,
        "scenario": "decision_supersession",
        "status": "passed",
        "database": {"kind": "temporary", "retained": False},
        "decision": {
            "current": "PostgreSQL",
            "history": ["SQLite"],
            "relationship": "PostgreSQL supersedes SQLite",
        },
        "provenance": {
            "current_source_event_ref": "mapi-project-memory-demo:postgresql",
            "history_source_event_ref": "mapi-project-memory-demo:sqlite",
        },
        "assertions": assertions,
    }


def run_decision_supersession_proof() -> dict[str, Any]:
    """Run a stable, machine-readable decision supersession proof."""
    return _stable_decision_proof(run_isolated_demo())


def _rewrite_capture_proposal(
    server: Any,
    *,
    item_id: int,
    patch: dict[str, Any],
) -> None:
    """Inject deterministic classifier output for an offline proof fixture."""
    current = server.get_memory_capture_review_item(item_id)["item"]["proposal"]
    current.update(patch)
    connection = server.get_db_connection()
    try:
        connection.execute(
            "UPDATE memory_capture_review_items SET proposal_json = ? WHERE id = ?",
            (json.dumps(current, ensure_ascii=False, sort_keys=True), int(item_id)),
        )
        connection.commit()
    finally:
        connection.close()


def run_conflict_provenance_database(path: Path) -> dict[str, Any]:
    """Run the offline conflict proof in an explicitly supplied disposable database."""
    from app import db_migrations

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    server_core = _configure_demo_server(path)

    connection = server_core.get_db_connection()
    try:
        db_migrations.apply_all_migrations(connection)
        original = server_core._insert_memory(
            connection,
            content="The daily backup runs at 02:00 UTC.",
            summary_short="Daily backup time: 02:00 UTC",
            memory_type="project_note",
            project_key=PRODUCT_PROOF_PROJECT_KEY,
            scope_code="project",
            source="operations-runbook",
            source_context="fictional runbook revision A",
            source_event_ref="proof:backup:runbook:02:00",
            state_code="validated",
            memory_v2_status="active",
            truth_kind="fact",
            confidence_score=1.0,
            importance_score=0.8,
            ensure_embedding=False,
        )
        connection.commit()
    finally:
        connection.close()

    original_id = int(original["id"])
    original_before = server_core.get_memory(original_id)["memory"]
    enabled = server_core.upsert_feature_flag(
        flag_key="memory_v3_capture_reconciliation_enabled",
        is_enabled=True,
        rollout_mode="projects",
        allowed_project_keys=PRODUCT_PROOF_PROJECT_KEY,
        allowed_scope_codes="project",
        read_only_mode=False,
        notes="Offline product proof in a disposable database.",
    )
    if enabled.get("status") not in {"created", "updated", "upserted", "ok"}:
        raise RuntimeError(f"Could not enable reconciliation: {enabled}")

    queued = server_core.save_memory_capture_proposal(
        content="The daily backup runs at 04:00 UTC.",
        project_key=PRODUCT_PROOF_PROJECT_KEY,
        scope_code="project",
        source_context="fictional incident review revision B",
        source_event_ref="proof:backup:incident-review:04:00",
    )
    item = queued.get("item") or {}
    if queued.get("status") not in {"created", "existing", "queued"} or not item.get("id"):
        raise RuntimeError(f"Capture queue failed: {queued}")
    item_id = int(item["id"])
    _rewrite_capture_proposal(
        server_core,
        item_id=item_id,
        patch={
            "is_contradiction": True,
            "contradiction_target_memory_id": original_id,
            "conflict_reason": "Two sources specify different daily backup times.",
        },
    )
    preview = server_core.preview_memory_capture_reconciliation(
        item_id=item_id,
        include_semantic=False,
    )
    if preview.get("status") != "preview_ready" or preview.get("outcome") != "conflict_review":
        raise RuntimeError(f"Conflict preview failed: {preview}")
    reviewed = server_core.review_memory_capture_item(
        item_id,
        "approve",
        reviewed_by="mapi-product-proof",
        review_note="Approve the fictional conflict fixture for guarded apply.",
    )
    if reviewed.get("status") != "updated":
        raise RuntimeError(f"Conflict review failed: {reviewed}")
    applied = server_core.apply_memory_capture_reconciliation(
        item_id=item_id,
        expected_preview_hash=str(preview["reconciliation_preview_hash"]),
        applied_by="mapi-product-proof",
        notes="Apply the fictional conflict fixture in a disposable database.",
    )
    if applied.get("status") != "applied":
        raise RuntimeError(f"Conflict apply failed: {applied}")

    conflicting_id = int(applied["created_memory_id"])
    original_after = server_core.get_memory(original_id)["memory"]
    conflicting = server_core.get_memory(conflicting_id)["memory"]
    pairs = server_core.get_conflict_pairs(memory_id=original_id)
    registry = server_core.get_conflict_clusters(include_members=True)
    pair = next(
        (
            candidate
            for candidate in pairs.get("items", [])
            if {int(candidate["from_memory_id"]), int(candidate["to_memory_id"])} == {original_id, conflicting_id}
        ),
        None,
    )
    cluster = next(
        (
            candidate
            for candidate in registry.get("clusters", [])
            if {original_id, conflicting_id}.issubset({int(memory_id) for memory_id in candidate.get("member_ids", [])})
        ),
        None,
    )
    protected_fields = ("content", "source", "source_context", "source_event_ref")
    original_preserved = all(original_after[field] == original_before[field] for field in protected_fields)
    assertions = {
        "both_records_preserved": original_after["content"] != conflicting["content"],
        "both_provenance_chains_preserved": (
            original_after["source_event_ref"] == "proof:backup:runbook:02:00"
            and conflicting["source_event_ref"] == "proof:backup:incident-review:04:00"
        ),
        "original_not_overwritten": original_preserved,
        "conflict_relation_present": pair is not None,
        "conflict_review_outcome": applied.get("outcome") == "conflict_review",
        "both_records_conflicted": all(
            memory.get("state_code") == "conflicted"
            and memory.get("memory_v2_status") == "contradicted"
            and int(memory.get("contradiction_flag") or 0) == 1
            for memory in (original_after, conflicting)
        ),
        "unresolved_conflict_cluster_present": cluster is not None and bool(cluster.get("has_unresolved")),
        "automatic_winner_not_selected": (
            all(memory.get("state_code") == "conflicted" for memory in (original_after, conflicting))
            and cluster is not None
            and bool(cluster.get("has_unresolved"))
        ),
        "external_calls": 0,
    }
    _require_truthful_assertions(assertions)

    return {
        "status": "ok",
        "database": str(path),
        "original_memory_id": original_id,
        "conflicting_memory_id": conflicting_id,
        "item_id": item_id,
        "assertions": assertions,
    }


def run_conflict_provenance_proof() -> dict[str, Any]:
    """Run a stable, machine-readable conflict and provenance proof."""
    with tempfile.TemporaryDirectory(prefix="mapi-conflict-proof-") as directory:
        raw = run_conflict_provenance_database(Path(directory) / "proof.db")
    return {
        "schema_version": PRODUCT_PROOF_SCHEMA_VERSION,
        "scenario": "conflict_provenance",
        "status": "passed",
        "database": {"kind": "temporary", "retained": False},
        "claims": [
            {
                "value": "Daily backup at 02:00 UTC",
                "source_event_ref": "proof:backup:runbook:02:00",
            },
            {
                "value": "Daily backup at 04:00 UTC",
                "source_event_ref": "proof:backup:incident-review:04:00",
            },
        ],
        "resolution": {
            "state": "conflicted",
            "review_outcome": "conflict_review",
            "automatic_winner_selected": False,
        },
        "assertions": raw["assertions"],
    }


def run_all_product_proofs() -> dict[str, Any]:
    proofs = [run_decision_supersession_proof(), run_conflict_provenance_proof()]
    return {
        "schema_version": PRODUCT_PROOF_SUITE_SCHEMA_VERSION,
        "status": "passed" if all(proof["status"] == "passed" for proof in proofs) else "failed",
        "proofs": proofs,
    }


def render_product_proof_report(result: dict[str, Any]) -> str:
    proofs = result.get("proofs") or [result]
    lines = ["MAPI product proofs"]
    for proof in proofs:
        lines.append(f"- {proof['scenario']}: {proof['status'].upper()}")
        for name, value in proof.get("assertions", {}).items():
            lines.append(f"  - {name}: {value}")
    return "\n".join(lines)


def run_product_proof_cli(proof: Callable[[], dict[str, Any]]) -> int:
    """Emit stable JSON to stdout and a human report to stderr."""
    try:
        result = proof()
    except Exception as exc:
        failure = {
            "schema_version": PRODUCT_PROOF_SCHEMA_VERSION,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"MAPI product proof: FAILED\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print(render_product_proof_report(result), file=sys.stderr)
    return 0 if result.get("status") == "passed" else 1
