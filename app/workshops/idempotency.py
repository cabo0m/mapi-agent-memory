from __future__ import annotations

import hashlib
import inspect
import json
from functools import wraps
from typing import Any, Callable, Mapping


IDEMPOTENCY_SCHEMA = "mapi_idempotency.v1"
IDEMPOTENCY_STATUSES = ("started", "completed", "in_doubt")
MAX_IDEMPOTENCY_KEY_LENGTH = 200


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        raise ValueError("idempotency_key_empty")
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValueError("idempotency_key_too_long")
    return key


def idempotency_error_payload(error: str, *, key: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "error": error,
        "idempotency": {
            "schema": IDEMPOTENCY_SCHEMA,
            "key": key,
        },
    }
    payload.update(extra)
    return payload


def prepare_request(
    conn: Any,
    *,
    idempotency_key: str,
    operation_name: str,
    request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    key = normalize_idempotency_key(idempotency_key)
    if key is None:
        raise ValueError("idempotency_key_empty")
    fingerprint = payload_fingerprint(request_payload)
    row = conn.execute(
        "SELECT * FROM mcp_idempotency_requests WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if row is not None:
        item = dict(row)
        if str(item.get("operation_name")) != str(operation_name) or str(item.get("payload_fingerprint")) != fingerprint:
            return {
                "status": "conflict",
                "schema": IDEMPOTENCY_SCHEMA,
                "idempotency_key": key,
                "operation_name": operation_name,
                "stored_operation_name": item.get("operation_name"),
                "payload_fingerprint": fingerprint,
                "stored_payload_fingerprint": item.get("payload_fingerprint"),
            }
        status = str(item.get("status") or "")
        if status == "completed":
            try:
                result = json.loads(str(item.get("result_json") or "null"))
            except json.JSONDecodeError:
                return {
                    "status": "in_doubt",
                    "schema": IDEMPOTENCY_SCHEMA,
                    "idempotency_key": key,
                    "operation_name": operation_name,
                    "reason": "stored_result_invalid_json",
                }
            return {
                "status": "replay",
                "schema": IDEMPOTENCY_SCHEMA,
                "idempotency_key": key,
                "operation_name": operation_name,
                "payload_fingerprint": fingerprint,
                "result": result,
                "completed_at": item.get("completed_at"),
            }
        return {
            "status": "in_doubt",
            "schema": IDEMPOTENCY_SCHEMA,
            "idempotency_key": key,
            "operation_name": operation_name,
            "payload_fingerprint": fingerprint,
            "stored_status": status,
            "reason": "request_started_without_completed_result" if status == "started" else "request_marked_in_doubt",
            "started_at": item.get("started_at"),
            "updated_at": item.get("updated_at"),
            "error_type": item.get("error_type"),
        }

    try:
        conn.execute(
            """
            INSERT INTO mcp_idempotency_requests (
                idempotency_key, operation_name, payload_fingerprint, status,
                result_json, error_type, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, 'started', NULL, NULL, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP)
            """,
            (key, str(operation_name), fingerprint),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        row = conn.execute(
            "SELECT * FROM mcp_idempotency_requests WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise
        return prepare_request(
            conn,
            idempotency_key=key,
            operation_name=operation_name,
            request_payload=request_payload,
        )
    return {
        "status": "execute",
        "schema": IDEMPOTENCY_SCHEMA,
        "idempotency_key": key,
        "operation_name": operation_name,
        "payload_fingerprint": fingerprint,
    }


def complete_request(conn: Any, *, idempotency_key: str, result: Any) -> None:
    encoded = _canonical_json(result)
    cursor = conn.execute(
        """
        UPDATE mcp_idempotency_requests
        SET status='completed', result_json=?, error_type=NULL,
            completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
        WHERE idempotency_key=? AND status='started'
        """,
        (encoded, idempotency_key),
    )
    if int(cursor.rowcount or 0) != 1:
        conn.rollback()
        raise RuntimeError("idempotency_completion_state_mismatch")
    conn.commit()


def mark_in_doubt(conn: Any, *, idempotency_key: str, error_type: str) -> None:
    conn.execute(
        """
        UPDATE mcp_idempotency_requests
        SET status='in_doubt', error_type=?, updated_at=CURRENT_TIMESTAMP
        WHERE idempotency_key=? AND status='started'
        """,
        (str(error_type), idempotency_key),
    )
    conn.commit()


def attach_idempotency_metadata(
    result: Any,
    *,
    key: str,
    operation_name: str,
    payload_fingerprint_value: str,
    replayed: bool,
) -> Any:
    metadata = {
        "schema": IDEMPOTENCY_SCHEMA,
        "key": key,
        "operation_name": operation_name,
        "payload_fingerprint": payload_fingerprint_value,
        "replayed": bool(replayed),
    }
    if isinstance(result, dict):
        copied = dict(result)
        copied["idempotency"] = metadata
        return copied
    return {"status": "ok", "result": result, "idempotency": metadata}


def execute_idempotent(
    *,
    get_db_connection: Callable[[], Any],
    idempotency_key: str | None,
    operation_name: str,
    request_payload: Mapping[str, Any],
    handler: Callable[[], Any],
) -> Any:
    try:
        key = normalize_idempotency_key(idempotency_key)
    except ValueError as exc:
        return idempotency_error_payload(str(exc), key=idempotency_key)
    if key is None:
        return handler()

    conn = get_db_connection()
    try:
        prepared = prepare_request(
            conn,
            idempotency_key=key,
            operation_name=operation_name,
            request_payload=request_payload,
        )
    finally:
        conn.close()

    if prepared["status"] == "conflict":
        return idempotency_error_payload(
            "idempotency_key_conflict",
            key=key,
            operation_name=operation_name,
            stored_operation_name=prepared.get("stored_operation_name"),
        )
    if prepared["status"] == "in_doubt":
        return idempotency_error_payload(
            "idempotency_in_doubt",
            key=key,
            operation_name=operation_name,
            reason=prepared.get("reason"),
            stored_status=prepared.get("stored_status"),
            error_type=prepared.get("error_type"),
        )
    if prepared["status"] == "replay":
        return attach_idempotency_metadata(
            prepared["result"],
            key=key,
            operation_name=operation_name,
            payload_fingerprint_value=str(prepared["payload_fingerprint"]),
            replayed=True,
        )

    fingerprint = str(prepared["payload_fingerprint"])
    try:
        result = handler()
    except Exception as exc:
        conn = get_db_connection()
        try:
            mark_in_doubt(conn, idempotency_key=key, error_type=exc.__class__.__name__)
        finally:
            conn.close()
        raise

    conn = get_db_connection()
    try:
        complete_request(conn, idempotency_key=key, result=result)
    except Exception:
        try:
            mark_in_doubt(conn, idempotency_key=key, error_type="completion_failure")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return attach_idempotency_metadata(
        result,
        key=key,
        operation_name=operation_name,
        payload_fingerprint_value=fingerprint,
        replayed=False,
    )


def idempotent_direct_mutation(
    operation_name: str,
    *,
    get_db_connection_resolver: Callable[[], Callable[[], Any]],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(func)

        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            key = bound.arguments.get("idempotency_key")
            payload = {name: value for name, value in bound.arguments.items() if name != "idempotency_key"}
            return execute_idempotent(
                get_db_connection=get_db_connection_resolver(),
                idempotency_key=key,
                operation_name=operation_name,
                request_payload=payload,
                handler=lambda: func(*args, **kwargs),
            )

        return wrapped

    return decorator
