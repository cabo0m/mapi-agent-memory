"""Gemma runtime preflight for Sandman.

Odpowiada za jawne sprawdzenie dostępności Gemmy przed każdym runem Sandmana,
który deklaruje Gemma jako runtime. Brak Gemmy to błąd, nie sugestia.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

SANDMAN_GEMMA_REQUIRED: bool = os.environ.get("SANDMAN_GEMMA_REQUIRED", "true").strip().lower() in {"1", "true", "yes", "on"}
SANDMAN_GEMMA_AUTOSTART: bool = os.environ.get("SANDMAN_GEMMA_AUTOSTART", "true").strip().lower() in {"1", "true", "yes", "on"}
SANDMAN_GEMMA_FAIL_CLOSED: bool = os.environ.get("SANDMAN_GEMMA_FAIL_CLOSED", "true").strip().lower() in {"1", "true", "yes", "on"}

SANDMAN_GEMMA_BASE_URL: str = os.environ.get("SANDMAN_GEMMA_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
SANDMAN_GEMMA_MODEL: str = os.environ.get("SANDMAN_GEMMA_MODEL", "google/gemma-4-e2b")
SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS: int = int(os.environ.get("SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS", "20"))
SANDMAN_GEMMA_START_TIMEOUT_SECONDS: int = int(os.environ.get("SANDMAN_GEMMA_START_TIMEOUT_SECONDS", "90"))
SANDMAN_GEMMA_LMS_TTL_SECONDS: int = int(os.environ.get("SANDMAN_GEMMA_LMS_TTL_SECONDS", "300"))

# Identyfikator pod którym model jest załadowany w LM Studio (używany przez lms load --identifier)
SANDMAN_GEMMA_LMS_IDENTIFIER: str = os.environ.get("SANDMAN_GEMMA_LMS_IDENTIFIER", "sandman-gemma")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_loaded_models(timeout: int = 10) -> list[str]:
    """Zwraca listę id modeli aktualnie widocznych przez /v1/models."""
    try:
        req = urllib.request.Request(
            f"{SANDMAN_GEMMA_BASE_URL}/models",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data") or []
        return [str(m.get("id") or "") for m in models if isinstance(m, dict)]
    except Exception as exc:
        raise ConnectionError(
            f"LM Studio /v1/models niedostepny pod {SANDMAN_GEMMA_BASE_URL}. "
            f"Czy serwer dziala? Szczegoly: {exc}"
        ) from exc


def _model_available(model_id: str, loaded_models: list[str]) -> bool:
    """Sprawdza, czy model lub identifier jest widoczny na liscie."""
    needle = model_id.lower()
    return any(needle in m.lower() or m.lower() in needle for m in loaded_models)


def _test_chat_completion(model_id: str, timeout: int = 30) -> str:
    """Minimalny test /chat/completions. Zwraca pierwsza odpowiedź modelu."""
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Odpowiedz jednym slowem: dziala"}],
        "temperature": 0.0,
        "max_tokens": 10,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SANDMAN_GEMMA_BASE_URL}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        choices = result.get("choices") or []
        if not choices:
            raise ValueError(f"Brak choices w odpowiedzi: {result}")
        return str((choices[0].get("message") or {}).get("content") or "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ConnectionError(
            f"LM Studio /chat/completions zwrocilo HTTP {exc.code} dla {model_id}. "
            f"Tresc: {body[:800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(
            f"Nie mozna polaczyc sie z LM Studio pod {SANDMAN_GEMMA_BASE_URL}: {exc}"
        ) from exc


def _try_autostart() -> dict[str, Any]:
    """Probuje uruchomic serwer LM Studio i zaladowac model via lms CLI."""
    result: dict[str, Any] = {"server_start": None, "model_load": None, "errors": []}

    # 1. lms server start
    try:
        proc = subprocess.run(
            ["lms", "server", "start"],
            capture_output=True, text=True,
            timeout=SANDMAN_GEMMA_START_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        # "already running" to tez OK
        if proc.returncode == 0 or "already" in stdout.lower() or "already" in stderr.lower() or "running" in stdout.lower():
            result["server_start"] = {"status": "ok", "stdout": stdout}
        else:
            result["server_start"] = {"status": "failed", "returncode": proc.returncode, "stderr": stderr}
            result["errors"].append(f"lms server start failed: {stderr[:400]}")
    except FileNotFoundError:
        result["server_start"] = {"status": "lms_not_found"}
        result["errors"].append("Komenda 'lms' nie jest dostepna na PATH.")
        return result
    except subprocess.TimeoutExpired:
        result["server_start"] = {"status": "timeout"}
        result["errors"].append("lms server start timeout.")
        return result

    # 2. lms load
    try:
        args = [
            "lms", "load", SANDMAN_GEMMA_MODEL,
            "--identifier", SANDMAN_GEMMA_LMS_IDENTIFIER,
            "--ttl", str(SANDMAN_GEMMA_LMS_TTL_SECONDS),
            "--gpu", "max",
            "--yes",
        ]
        proc = subprocess.run(
            args,
            capture_output=True, text=True,
            timeout=SANDMAN_GEMMA_START_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode == 0 or "already" in stdout.lower() or "already" in stderr.lower():
            result["model_load"] = {"status": "ok", "stdout": stdout}
        else:
            result["model_load"] = {"status": "failed", "returncode": proc.returncode, "stderr": stderr}
            result["errors"].append(f"lms load failed: {stderr[:400]}")
    except subprocess.TimeoutExpired:
        result["model_load"] = {"status": "timeout"}
        result["errors"].append("lms load timeout.")

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_gemma_ready(
    *,
    required: bool | None = None,
    fail_closed: bool | None = None,
    autostart: bool | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Sprawdza, czy Gemma jest dostepna jako Sandman runtime.

    Jezeli required=True (domyslnie z env) i Gemma nie dziala, rzuca RuntimeError.
    Nigdy nie robi cichego fallbacku na inny model.

    Zwraca slownik diagnostyczny z polami:
      - status: "ok" | "failed"
      - model: nazwa modelu
      - base_url: adres endpointu
      - loaded_models: lista widocznych modeli
      - chat_test_response: krotka odpowiedz test
      - autostart_result: slownik jezeli autostart byl wywolany
      - error: opis bledu jezeli status="failed"
    """
    _required = required if required is not None else SANDMAN_GEMMA_REQUIRED
    _fail_closed = fail_closed if fail_closed is not None else SANDMAN_GEMMA_FAIL_CLOSED
    _autostart = autostart if autostart is not None else SANDMAN_GEMMA_AUTOSTART
    _model = model or SANDMAN_GEMMA_LMS_IDENTIFIER  # identifier zaladowany przez lms load

    result: dict[str, Any] = {
        "status": "unknown",
        "model": _model,
        "configured_model_key": SANDMAN_GEMMA_MODEL,
        "base_url": SANDMAN_GEMMA_BASE_URL,
        "loaded_models": [],
        "model_visible": False,
        "chat_test_response": None,
        "autostart_result": None,
        "error": None,
    }

    def _fail(msg: str) -> dict[str, Any]:
        result["status"] = "failed"
        result["error"] = msg
        if _fail_closed:
            raise RuntimeError(
                f"SANDMAN_GEMMA_FAIL_CLOSED=true: Gemma runtime niedostepna. {msg}\n"
                f"Model: {_model}, URL: {SANDMAN_GEMMA_BASE_URL}\n"
                "Uruchom: lms server start && lms load google/gemma-4-e2b --identifier sandman-gemma --gpu max --yes"
            )
        return result

    # --- 1. Sprawdz /v1/models ---
    try:
        loaded = _get_loaded_models(timeout=SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS)
        result["loaded_models"] = loaded
    except ConnectionError as exc:
        if _autostart:
            result["autostart_result"] = _try_autostart()
            try:
                loaded = _get_loaded_models(timeout=SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS)
                result["loaded_models"] = loaded
            except ConnectionError as exc2:
                return _fail(f"Po autostart serwer nadal niedostepny: {exc2}")
        else:
            return _fail(str(exc))

    # --- 2. Sprawdz widocznosc modelu ---
    visible = _model_available(_model, result["loaded_models"])
    result["model_visible"] = visible

    if not visible:
        if _autostart and result["autostart_result"] is None:
            result["autostart_result"] = _try_autostart()
            try:
                loaded = _get_loaded_models(timeout=SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS)
                result["loaded_models"] = loaded
                visible = _model_available(_model, loaded)
                result["model_visible"] = visible
            except ConnectionError as exc:
                return _fail(f"Po autostart: {exc}")

        if not visible:
            return _fail(
                f"Model '{_model}' nie jest widoczny w /v1/models. "
                f"Zaladowane: {result['loaded_models']}. "
                f"Wykonaj: lms load {SANDMAN_GEMMA_MODEL} --identifier {_model} --gpu max --yes"
            )

    # --- 3. Test chat completion ---
    try:
        test_response = _test_chat_completion(_model, timeout=SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS)
        result["chat_test_response"] = test_response
    except Exception as exc:
        return _fail(f"Test /chat/completions nieudany: {exc}")

    result["status"] = "ok"
    return result


def gemma_runtime_info() -> dict[str, Any]:
    """Zwraca konfiguracje Gemma runtime bez zadnych wywolan sieciowych."""
    return {
        "required": SANDMAN_GEMMA_REQUIRED,
        "autostart": SANDMAN_GEMMA_AUTOSTART,
        "fail_closed": SANDMAN_GEMMA_FAIL_CLOSED,
        "base_url": SANDMAN_GEMMA_BASE_URL,
        "model_key": SANDMAN_GEMMA_MODEL,
        "lms_identifier": SANDMAN_GEMMA_LMS_IDENTIFIER,
        "health_timeout_seconds": SANDMAN_GEMMA_HEALTH_TIMEOUT_SECONDS,
        "start_timeout_seconds": SANDMAN_GEMMA_START_TIMEOUT_SECONDS,
    }
