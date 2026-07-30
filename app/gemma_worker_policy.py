from __future__ import annotations

"""Policy guardrails for Gemma Worker job execution."""

from pathlib import Path, PurePath
from typing import Any

MVP_ALLOWED_ACTIONS = {
    "read_file",
    "list_files",
    "search_files",
    "propose_patch",
    "run_tests",
}

_SECRET_PATH_PARTS = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "secrets",
    ".ssh",
}


class GemmaWorkerPolicyError(ValueError):
    """Raised when Gemma Worker policy denies an action or path."""


def staged_action_hint() -> str:
    return "Use Gemma Worker staged action names, not legacy admin tool names."


def allowed_actions_list() -> list[str]:
    return sorted(MVP_ALLOWED_ACTIONS)


def policy_denied_payload(
    *,
    details: str,
    stage: str | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "error",
        "error": "policy_denied",
        "details": details,
        "allowed_actions": allowed_actions_list(),
        "hint": staged_action_hint(),
    }
    if stage is not None:
        payload["stage"] = stage
    return payload


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_required_text(value: Any, field_name: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is None:
        raise GemmaWorkerPolicyError(f"{field_name} is required")
    return normalized


def normalize_allowed_actions(actions: list[str] | None) -> list[str]:
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise GemmaWorkerPolicyError("allowed_actions must be a JSON array")
    normalized: list[str] = []
    for index, action in enumerate(actions):
        action_name = normalize_optional_text(action)
        if action_name is None:
            raise GemmaWorkerPolicyError(
                f"allowed_actions[{index}] must be a non-empty string"
            )
        normalized.append(action_name)
    return normalized


def validate_allowed_actions(actions: list[str] | None) -> list[str]:
    normalized = normalize_allowed_actions(actions)
    rejected = sorted(set(normalized) - MVP_ALLOWED_ACTIONS)
    if rejected:
        raise GemmaWorkerPolicyError(
            "disallowed actions requested: " + ", ".join(rejected)
        )
    return normalized


def validate_repo_root(repo: str) -> Path:
    repo_text = normalize_required_text(repo, "repo")
    try:
        repo_path = Path(repo_text).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise GemmaWorkerPolicyError("repo must point to an existing directory") from exc
    if not repo_path.is_dir():
        raise GemmaWorkerPolicyError("repo must point to an existing directory")
    return repo_path


def _is_secret_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    return any(part in _SECRET_PATH_PARTS for part in lowered_parts)


def validate_repo_path(
    repo: str,
    target_path: str,
    *,
    allow_missing: bool = True,
) -> Path:
    repo_root = validate_repo_root(repo)
    target_text = normalize_required_text(target_path, "target_path")
    pure_target = PurePath(target_text)
    if ".." in pure_target.parts:
        raise GemmaWorkerPolicyError("target_path must not contain '..'")

    raw_target = Path(target_text).expanduser()
    resolved = raw_target.resolve(strict=False) if raw_target.is_absolute() else (repo_root / raw_target).resolve(strict=False)
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise GemmaWorkerPolicyError("target_path escapes repo root") from exc
    if _is_secret_path(resolved.relative_to(repo_root)):
        raise GemmaWorkerPolicyError("target_path points to a protected secret path")
    if not allow_missing and not resolved.exists():
        raise GemmaWorkerPolicyError("target_path does not exist")
    return resolved


def build_job_policy(repo: str, allowed_actions: list[str] | None) -> dict[str, Any]:
    repo_root = validate_repo_root(repo)
    actions = validate_allowed_actions(allowed_actions)
    return {
        "repo_root": str(repo_root),
        "allowed_actions": actions,
        "mode": "mvp",
    }
