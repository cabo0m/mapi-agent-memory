from __future__ import annotations

from pathlib import Path

import pytest

import mapi.system_install as system_install
from mapi.system_install import (
    CommandResult,
    install_systemd_maintenance_timer,
    install_systemd_service,
    maintenance_unit_names,
    mcp_connection_urls,
)


def test_connection_urls_select_public_origin_when_configured() -> None:
    urls = mcp_connection_urls(public_origin="https://mapi.example.test", port=8015)
    assert urls == {
        "loopback_mcp_url": "http://127.0.0.1:8015/mcp/",
        "public_mcp_url": "https://mapi.example.test/mcp/",
        "recommended_mcp_url": "https://mapi.example.test/mcp/",
    }


def test_connection_urls_use_loopback_for_local_instance() -> None:
    urls = mcp_connection_urls(public_origin=None, port=9123)
    assert urls["recommended_mcp_url"] == "http://127.0.0.1:9123/mcp/"
    assert urls["public_mcp_url"] is None


def test_systemd_install_executes_install_reload_enable_and_checks_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unit = tmp_path / "mapi.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv):
        values = [str(item) for item in argv]
        calls.append(values)
        if values[-2:] == ["is-active", "mapi.service"]:
            return CommandResult(tuple(values), 0, "active\n", "")
        return CommandResult(tuple(values), 0, "", "")

    monkeypatch.setattr(system_install, "systemd_available", lambda: True)
    monkeypatch.setattr(system_install, "_privilege_prefix", lambda allow_prompt: ["sudo"])
    result = install_systemd_service(unit, allow_sudo_prompt=True, runner=runner)

    assert result["status"] == "active"
    assert result["active"] is True
    assert calls[0][:4] == ["sudo", "install", "-m", "0644"]
    assert calls[1] == ["sudo", "systemctl", "daemon-reload"]
    assert calls[2] == ["sudo", "systemctl", "enable", "--now", "mapi.service"]
    assert calls[3] == ["systemctl", "is-active", "mapi.service"]


def test_systemd_install_fails_closed_when_enable_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unit = tmp_path / "mapi.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")

    def runner(argv):
        values = [str(item) for item in argv]
        code = 1 if "enable" in values else 0
        return CommandResult(tuple(values), code, "", "failed" if code else "")

    monkeypatch.setattr(system_install, "systemd_available", lambda: True)
    monkeypatch.setattr(system_install, "_privilege_prefix", lambda allow_prompt: [])
    with pytest.raises(RuntimeError, match="systemd_install_failed"):
        install_systemd_service(unit, allow_sudo_prompt=False, runner=runner)


def test_systemd_exec_start_preserves_virtualenv_interpreter_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import mapi.initialize as init_module

    monkeypatch.setattr(init_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(init_module.sys, "executable", "venv-python")
    assert init_module._systemd_exec_start().startswith('venv-python -c ')
    assert str(Path.cwd()) not in init_module._systemd_exec_start()


def test_systemd_install_supports_custom_service_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unit = tmp_path / "polaris.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv):
        values = [str(item) for item in argv]
        calls.append(values)
        if values[-2:] == ["is-active", "polaris.service"]:
            return CommandResult(tuple(values), 0, "active\n", "")
        return CommandResult(tuple(values), 0, "", "")

    monkeypatch.setattr(system_install, "systemd_available", lambda: True)
    monkeypatch.setattr(system_install, "_privilege_prefix", lambda allow_prompt: [])
    result = install_systemd_service(
        unit,
        service_name="polaris",
        allow_sudo_prompt=False,
        runner=runner,
    )

    assert result["service_name"] == "polaris.service"
    assert result["unit_destination"].replace("\\", "/") == "/etc/systemd/system/polaris.service"
    assert calls[2] == ["systemctl", "enable", "--now", "polaris.service"]
    assert calls[3] == ["systemctl", "is-active", "polaris.service"]


def test_invalid_systemd_service_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid_systemd_service_name"):
        system_install.normalize_systemd_service_name("../wrong")


def test_maintenance_unit_names_follow_main_service_name() -> None:
    assert maintenance_unit_names("polaris") == ("polaris-maintenance.service", "polaris-maintenance.timer")
    assert maintenance_unit_names("client-a.service") == ("client-a-maintenance.service", "client-a-maintenance.timer")


def test_systemd_maintenance_timer_installs_and_enables_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = tmp_path / "polaris-maintenance.service"
    timer = tmp_path / "polaris-maintenance.timer"
    service.write_text("[Service]\nType=oneshot\nExecStart=/bin/true\n", encoding="utf-8")
    timer.write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv):
        values = [str(item) for item in argv]
        calls.append(values)
        if values[-2:] == ["is-enabled", "polaris-maintenance.timer"]:
            return CommandResult(tuple(values), 0, "enabled\n", "")
        if values[-2:] == ["is-active", "polaris-maintenance.timer"]:
            return CommandResult(tuple(values), 0, "active\n", "")
        return CommandResult(tuple(values), 0, "", "")

    monkeypatch.setattr(system_install, "systemd_available", lambda: True)
    monkeypatch.setattr(system_install, "_privilege_prefix", lambda allow_prompt: [])
    result = install_systemd_maintenance_timer(
        service,
        timer,
        service_name="polaris",
        allow_sudo_prompt=False,
        runner=runner,
    )

    assert result["status"] == "active"
    assert result["timer_name"] == "polaris-maintenance.timer"
    assert calls[0][-1].replace("\\", "/") == "/etc/systemd/system/polaris-maintenance.service"
    assert calls[1][-1].replace("\\", "/") == "/etc/systemd/system/polaris-maintenance.timer"
    assert calls[3] == ["systemctl", "enable", "--now", "polaris-maintenance.timer"]


def test_maintenance_systemd_unit_preserves_virtualenv_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mapi.initialize as init_module

    monkeypatch.setattr(init_module.sys, "executable", "venv-python")
    unit = init_module.render_maintenance_systemd_unit(
        root=tmp_path / "runtime",
        env_file=tmp_path / "runtime" / ".env",
        service_user="mapi",
        service_name="polaris",
    )
    assert "ExecStart=venv-python -m mapi.maintenance" in unit
    assert "/usr/bin/python" not in unit