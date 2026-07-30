from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import audit_public_repository as public_audit


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
EXAMPLE_MODULE = re.compile(
    r"<!-- example-module: (?P<path>[^ ]+) -->\s*"
    r"```python\n(?P<source>.*?)\n```",
    re.DOTALL,
)


def _markdown_files() -> list[Path]:
    return sorted(
        [*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")],
        key=lambda path: path.as_posix(),
    )


def test_readme_quickstart_matches_console_entry_points() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = metadata["project"]["scripts"]
    required = {
        "mapi-server": "mapi.cli:server",
        "mapi-migrate": "mapi.cli:migrate",
        "mapi-doctor": "mapi.cli:doctor",
        "mapi-seed-demo": "mapi.cli:seed_demo",
        "mapi-capabilities": "mapi.capabilities:main",
    }
    assert scripts == required
    for command in ("mapi-migrate", "mapi-seed-demo", "mapi-doctor", "mapi-server"):
        assert command in readme
    assert "pip install -e ." in readme


def test_all_local_markdown_links_exist() -> None:
    failures: list[str] = []
    for document in _markdown_files():
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert failures == []


def test_mermaid_blocks_are_closed_and_nonempty() -> None:
    failures: list[str] = []
    for document in _markdown_files():
        lines = document.read_text(encoding="utf-8").splitlines()
        in_mermaid = False
        content_lines = 0
        for line_number, line in enumerate(lines, start=1):
            if line.strip() == "```mermaid":
                if in_mermaid:
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}:nested")
                in_mermaid = True
                content_lines = 0
            elif in_mermaid and line.strip() == "```":
                if content_lines == 0:
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}:empty")
                in_mermaid = False
            elif in_mermaid and line.strip():
                content_lines += 1
        if in_mermaid:
            failures.append(f"{document.relative_to(ROOT)}:unclosed")
    assert failures == []


def test_implementation_guide_example_modules_compile() -> None:
    guide = (ROOT / "docs" / "IMPLEMENTATION_GUIDE.md").read_text(encoding="utf-8")
    modules = list(EXAMPLE_MODULE.finditer(guide))
    assert {match.group("path") for match in modules} == {
        "app/workshops/example/__init__.py",
        "app/workshops/example/manifest.py",
        "app/workshops/example/handlers.py",
    }
    for match in modules:
        compile(
            match.group("source"),
            f"docs/IMPLEMENTATION_GUIDE.md::{match.group('path')}",
            "exec",
        )


def test_public_documentation_is_english_only() -> None:
    failures: list[dict[str, str]] = []
    checked = public_audit._scan_language_policy(
        failures,
        public_audit._load_json(ROOT / "public_audit_allowlist.json"),
        public_audit._tracked_candidate_paths(),
    )
    assert checked >= 20
    assert failures == []


def test_license_and_public_email_are_consistent() -> None:
    failures: list[dict[str, str]] = []
    result = public_audit._scan_license_policy(
        failures,
        public_audit._tracked_candidate_paths(),
    )
    assert result["license"] == "Apache-2.0"
    assert result["license_sha256"] == public_audit.EXPECTED_APACHE_LICENSE_SHA256
    assert failures == []

    public_email = public_audit.ALLOWED_PUBLIC_EMAIL
    assert public_email in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    private_email = bytes.fromhex(public_audit.FORBIDDEN_PRIVATE_EMAIL_HEX).decode("ascii")
    for path in public_audit._tracked_candidate_paths():
        assert private_email.casefold() not in path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).casefold()


def test_clean_release_git_metadata_passes_public_policy() -> None:
    status = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    if status.strip():
        pytest.skip("Git metadata invariant is evaluated after the release tree is committed")
    failures: list[dict[str, str]] = []
    result = public_audit._scan_git_metadata(failures)
    assert result["commit_count"] == 1
    assert failures == []
