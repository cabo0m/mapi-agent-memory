from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from app import lm_studio_client

SANDMAN_GEMMA_TIMEOUT_SECONDS: int = int(os.environ.get("SANDMAN_GEMMA_TIMEOUT_SECONDS", "120"))
SANDMAN_GEMMA_MAX_TOKENS: int = int(os.environ.get("SANDMAN_GEMMA_MAX_TOKENS", "2048"))
SANDMAN_GEMMA_LMS_MODEL_KEY: str = os.environ.get(
    "SANDMAN_GEMMA_LMS_MODEL_KEY",
    os.environ.get("SANDMAN_GEMMA_MODEL", "google/gemma-4-e2b"),
)
SANDMAN_GEMMA_LMS_IDENTIFIER: str = os.environ.get("SANDMAN_GEMMA_LMS_IDENTIFIER", "sandman-gemma")
SANDMAN_GEMMA_LMS_TTL_SECONDS: int = int(os.environ.get("SANDMAN_GEMMA_LMS_TTL_SECONDS", "300"))
SANDMAN_GEMMA_LMS_GPU: str | None = os.environ.get("SANDMAN_GEMMA_LMS_GPU", "max")
SANDMAN_GEMMA_LMS_CONTEXT_LENGTH: str | None = os.environ.get("SANDMAN_GEMMA_LMS_CONTEXT_LENGTH")
SANDMAN_GEMMA_LMS_UNLOAD_AFTER_CALL: bool = os.environ.get("SANDMAN_GEMMA_LMS_UNLOAD_AFTER_CALL", "true").strip().lower() in {"1", "true", "yes", "on"}

_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "memory_id",
                    "issue_kind",
                    "proposed_action",
                    "confidence",
                    "risk",
                    "rationale",
                    "required_checks",
                ],
                "properties": {
                    "memory_id": {"type": "integer"},
                    "issue_kind": {
                        "type": "string",
                        "enum": [
                            "owner_gap",
                            "project_scope_mismatch",
                            "test_probe_noise",
                            "duplicate_candidate",
                            "consolidation_candidate",
                            "stale_memory",
                            "conflict_candidate",
                            "ingest_candidate",
                            "no_action",
                            "needs_human_review",
                        ],
                    },
                    "proposed_action": {
                        "type": "string",
                        "enum": [
                            "set_owner",
                            "normalize_project_scope",
                            "archive_as_noise",
                            "keep_as_evidence",
                            "consolidate_then_archive",
                            "consolidate",
                            "link_memories",
                            "promote_ingest",
                            "reject_ingest",
                            "keep",
                            "needs_human_review",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "rationale": {"type": "string"},
                    "required_checks": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def sandman_gemma_response_format() -> dict[str, Any]:
    """Return the JSON schema response_format used by LM Studio/OpenAI-compatible local runtimes."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sandman_gemma_decisions",
            "strict": False,  # strict=True causes empty content on thinking models (gemma-4-e2b)
            "schema": _DECISION_SCHEMA,
        },
    }


class LocalGemmaClient:
    """Preview-only local Gemma adapter for Sandman hygiene decisions.

    The model only proposes JSON decisions. Parsing, validation, and execution stay in host code.
    """

    def __init__(self, *, timeout_seconds: int | None = None, max_tokens: int | None = None, model: str | None = None) -> None:
        self.timeout_seconds = int(timeout_seconds or SANDMAN_GEMMA_TIMEOUT_SECONDS)
        self.max_tokens = int(max_tokens or SANDMAN_GEMMA_MAX_TOKENS)
        self.model = model

    def complete_json(self, prompt: str, *, timeout_seconds: int | None = None) -> str:
        timeout = int(timeout_seconds or self.timeout_seconds)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Gemma acting as Sandman reviewer. Return exactly one raw JSON object "
                    "matching the provided schema. Do not execute actions. Do not include markdown."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        raw = lm_studio_client.call_lm_studio(
            messages,
            sandman_gemma_response_format(),
            max_tokens=self.max_tokens,
            timeout=timeout,
            model=self.model,
        )
        json.loads(raw)
        return raw


class LmsModelManager:
    """Small wrapper around the LM Studio CLI for on-demand model residency."""

    def __init__(
        self,
        *,
        model_key: str | None = None,
        identifier: str | None = None,
        ttl_seconds: int | None = None,
        gpu: str | None = None,
        context_length: str | None = None,
    ) -> None:
        self.model_key = model_key or SANDMAN_GEMMA_LMS_MODEL_KEY
        self.identifier = identifier or SANDMAN_GEMMA_LMS_IDENTIFIER
        self.ttl_seconds = int(ttl_seconds if ttl_seconds is not None else SANDMAN_GEMMA_LMS_TTL_SECONDS)
        self.gpu = gpu if gpu is not None else SANDMAN_GEMMA_LMS_GPU
        self.context_length = context_length if context_length is not None else SANDMAN_GEMMA_LMS_CONTEXT_LENGTH

    def _run_lms(self, args: list[str], *, timeout: int = 120) -> str:
        try:
            completed = subprocess.run(
                ["lms", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("LM Studio CLI 'lms' is not available on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"LM Studio CLI timed out while running: lms {' '.join(args)}") from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"lms {' '.join(args)} failed with exit code {completed.returncode}: {stderr[:1200]}")
        return completed.stdout

    def loaded_models(self) -> list[dict[str, Any]]:
        raw = self._run_lms(["ps", "--json"], timeout=30)
        parsed = json.loads(raw or "[]")
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("models", "loadedModels", "loaded_models"):
                items = parsed.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        raise ValueError(f"lms ps --json returned unsupported payload: {raw[:1200]}")

    def is_loaded(self) -> bool:
        needle = self.identifier.lower()
        for item in self.loaded_models():
            values = {str(value).lower() for value in item.values() if isinstance(value, str)}
            if needle in values:
                return True
        return False

    def ensure_loaded(self) -> dict[str, Any]:
        if self.is_loaded():
            return {"status": "already_loaded", "identifier": self.identifier, "model_key": self.model_key}
        args = ["load", self.model_key, "--identifier", self.identifier, "--ttl", str(self.ttl_seconds), "--yes"]
        if self.gpu:
            args.extend(["--gpu", self.gpu])
        if self.context_length:
            args.extend(["--context-length", str(self.context_length)])
        stdout = self._run_lms(args, timeout=600)
        return {
            "status": "loaded",
            "identifier": self.identifier,
            "model_key": self.model_key,
            "ttl_seconds": self.ttl_seconds,
            "stdout": stdout.strip(),
        }

    def unload(self) -> dict[str, Any]:
        if not self.is_loaded():
            return {"status": "not_loaded", "identifier": self.identifier}
        stdout = self._run_lms(["unload", self.identifier], timeout=120)
        return {"status": "unloaded", "identifier": self.identifier, "stdout": stdout.strip()}


def check_lms_status(*, manager: LmsModelManager | None = None) -> dict[str, Any]:
    """Return non-mutating diagnostics for the Sandman Gemma LM Studio residency setup."""
    resolved_manager = manager or LmsModelManager()
    status: dict[str, Any] = {
        "status": "unknown",
        "lm_studio_base_url": lm_studio_client.LM_STUDIO_BASE_URL,
        "lm_studio_default_model": lm_studio_client.LM_STUDIO_MODEL,
        "model_key": resolved_manager.model_key,
        "identifier": resolved_manager.identifier,
        "ttl_seconds": resolved_manager.ttl_seconds,
        "gpu": resolved_manager.gpu,
        "context_length": resolved_manager.context_length,
        "unload_after_call": SANDMAN_GEMMA_LMS_UNLOAD_AFTER_CALL,
        "loaded": False,
        "loaded_models": [],
        "error": None,
    }
    try:
        loaded_models = resolved_manager.loaded_models()
        status["loaded_models"] = loaded_models
        status["loaded"] = resolved_manager.is_loaded()
        status["status"] = "ok"
    except Exception as exc:
        status["status"] = "error"
        status["error"] = str(exc)
    return status


class ManagedLmsGemmaClient(LocalGemmaClient):
    """Preview-only Gemma client that loads the LM Studio model on demand via `lms load`."""

    def __init__(
        self,
        *,
        manager: LmsModelManager | None = None,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
        unload_after_call: bool | None = None,
    ) -> None:
        self.manager = manager or LmsModelManager()
        self.unload_after_call = SANDMAN_GEMMA_LMS_UNLOAD_AFTER_CALL if unload_after_call is None else bool(unload_after_call)
        super().__init__(timeout_seconds=timeout_seconds, max_tokens=max_tokens, model=self.manager.identifier)

    def complete_json(self, prompt: str, *, timeout_seconds: int | None = None) -> str:
        self.manager.ensure_loaded()
        try:
            return super().complete_json(prompt, timeout_seconds=timeout_seconds)
        finally:
            if self.unload_after_call:
                self.manager.unload()
