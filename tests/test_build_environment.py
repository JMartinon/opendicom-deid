"""Build entry points reject unusable Python before changing environments."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def build_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return (
        importlib.import_module("build_environment"),
        importlib.import_module("build_linux"),
    )


def environment(module, executable, *, error=None, base=None, version="3.14.4", venv=False):
    return module.BuildEnvironment(
        executable=executable,
        version=version,
        base_executable=base or executable,
        is_venv=venv,
        error=error,
        tcl_version="8.6.16" if error is None else None,
    )


def test_tcl_probe_does_not_require_a_display(build_modules, monkeypatch):
    checks, _ = build_modules
    monkeypatch.delenv("DISPLAY", raising=False)
    calls = []

    def forbidden_root(*args, **kwargs):
        pytest.fail("Build preflight must not create a Tk window")

    def evaluate(command):
        calls.append(command)
        return "8.6.16"

    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(
            Tcl=lambda: SimpleNamespace(eval=evaluate),
            Tk=forbidden_root,
            TclError=type("TclError", (Exception,), {}),
        ),
    )
    result = checks.inspect_environment()
    assert result.error is None
    assert result.tcl_version == "8.6.16"
    assert calls == ["info patchlevel"]


def test_broken_tcl_initialization_has_actionable_error(build_modules, monkeypatch):
    checks, _ = build_modules

    class TclError(Exception):
        pass

    def broken_tcl():
        raise TclError("Can't find a usable init.tcl")

    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(Tcl=broken_tcl, TclError=TclError),
    )
    result = checks.inspect_environment()
    assert result.error
    with pytest.raises(SystemExit) as failure:
        checks.require_build_environment()
    message = str(failure.value)
    assert sys.executable in message
    assert "Tk" in message
    assert "python" in message.lower()


def test_default_falls_back_when_active_python_has_no_tk(build_modules, monkeypatch):
    checks, linux = build_modules
    active = environment(checks, "/active/venv/bin/python3", error="No module named '_tkinter'")
    system = environment(checks, "/usr/bin/python3", version="3.12.3")
    attempted = []
    monkeypatch.setattr(linux.shutil, "which", lambda name: active.executable)

    def probe(executable):
        attempted.append(executable)
        return {active.executable: active, system.executable: system}[executable]

    monkeypatch.setattr(linux, "probe", probe)
    assert linux.select_python(None) == system
    assert attempted == [active.executable, system.executable]


def test_working_active_python_is_preferred(build_modules, monkeypatch):
    checks, linux = build_modules
    active = environment(checks, "/active/venv/bin/python3", venv=True)
    monkeypatch.setattr(linux.shutil, "which", lambda name: active.executable)

    def probe(executable):
        assert executable == active.executable
        return active

    monkeypatch.setattr(linux, "probe", probe)
    assert linux.select_python(None) == active


def test_explicit_python_never_silently_falls_back(build_modules, monkeypatch):
    checks, linux = build_modules
    requested = "/custom python/bin/python3"
    attempted = []

    def probe(executable):
        attempted.append(executable)
        return environment(checks, executable, error="No module named '_tkinter'")

    monkeypatch.setattr(linux, "probe", probe)
    with pytest.raises(SystemExit) as failure:
        linux.select_python(requested)
    assert attempted == [requested]
    assert requested in str(failure.value)


def test_failed_preflight_runs_no_installs_or_tests(build_modules, monkeypatch):
    checks, linux = build_modules
    monkeypatch.setattr(linux.sys, "platform", "linux")
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setattr(linux.shutil, "which", lambda name: "/active/bin/python3")
    monkeypatch.setattr(
        linux,
        "probe",
        lambda executable: environment(checks, executable, error="Tcl/Tk is missing"),
    )

    def forbidden_command(*args, **kwargs):
        pytest.fail("No installation, tests, or build should run after failed preflight")

    monkeypatch.setattr(linux.subprocess, "run", forbidden_command)
    with pytest.raises(SystemExit):
        linux.main()


def test_selected_active_venv_is_reused_without_creation(build_modules, monkeypatch, tmp_path):
    checks, linux = build_modules
    selected = environment(checks, "/active/venv/bin/python3", venv=True)
    monkeypatch.setattr(linux, "ROOT", tmp_path)

    def forbidden_command(*args, **kwargs):
        pytest.fail("A working selected venv must not be replaced")

    monkeypatch.setattr(linux.subprocess, "run", forbidden_command)
    assert linux.prepare_python(selected) == selected.executable
    assert not (tmp_path / ".venv-build-linux").exists()


def test_system_fallback_uses_isolated_build_venv(build_modules, monkeypatch, tmp_path):
    checks, linux = build_modules
    selected = environment(checks, "/usr/bin/python3", version="3.12.3")
    build_dir = tmp_path / ".venv-build-linux"
    build_python = build_dir / "bin" / "python"
    calls = []
    monkeypatch.setattr(linux, "ROOT", tmp_path)

    def run(command, **kwargs):
        calls.append(command)
        assert command[:3] == [selected.executable, "-m", "venv"]
        assert Path(command[-1]) == build_dir
        build_python.parent.mkdir(parents=True)
        build_python.touch()
        return SimpleNamespace(returncode=0)

    def probe(executable):
        assert Path(executable) == build_python
        return environment(
            checks,
            str(build_python),
            base=selected.executable,
            version=selected.version,
            venv=True,
        )

    monkeypatch.setattr(linux.subprocess, "run", run)
    monkeypatch.setattr(linux, "probe", probe)
    assert Path(linux.prepare_python(selected)) == build_python
    assert len(calls) == 1


def test_mismatched_build_venv_is_preserved(build_modules, monkeypatch, tmp_path):
    checks, linux = build_modules
    selected = environment(checks, "/usr/bin/python3", version="3.12.3")
    build_dir = tmp_path / ".venv-build-linux"
    build_python = build_dir / "bin" / "python"
    build_python.parent.mkdir(parents=True)
    build_python.write_text("existing environment", encoding="utf-8")
    monkeypatch.setattr(linux, "ROOT", tmp_path)
    monkeypatch.setattr(
        linux,
        "probe",
        lambda executable: environment(
            checks,
            str(build_python),
            base="/old/bin/python3",
            version="3.13.1",
            venv=True,
        ),
    )

    def forbidden_command(*args, **kwargs):
        pytest.fail("An incompatible existing build environment must not be mutated")

    monkeypatch.setattr(linux.subprocess, "run", forbidden_command)
    with pytest.raises(SystemExit) as failure:
        linux.prepare_python(selected)
    assert ".venv-build-linux" in str(failure.value)
    assert build_python.read_text(encoding="utf-8") == "existing environment"


def test_unsupported_python_is_rejected_before_tk_probe(build_modules, monkeypatch):
    checks, _ = build_modules
    monkeypatch.setattr(checks.sys, "version_info", (3, 11, 9))

    def forbidden_tcl():
        pytest.fail("Unsupported Python must be rejected before probing Tcl")

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tcl=forbidden_tcl))
    result = checks.inspect_environment()
    assert "3.12" in result.error


def test_compatible_build_venv_is_reused(build_modules, monkeypatch, tmp_path):
    checks, linux = build_modules
    selected = environment(checks, "/usr/bin/python3", version="3.12.3")
    build_dir = tmp_path / ".venv-build-linux"
    build_python = build_dir / "bin" / "python"
    build_python.parent.mkdir(parents=True)
    build_python.touch()
    monkeypatch.setattr(linux, "ROOT", tmp_path)
    monkeypatch.setattr(
        linux,
        "probe",
        lambda executable: environment(
            checks,
            str(build_python),
            base=selected.executable,
            version=selected.version,
            venv=True,
        ),
    )

    def forbidden_command(*args, **kwargs):
        pytest.fail("A compatible existing build environment must be reused")

    monkeypatch.setattr(linux.subprocess, "run", forbidden_command)
    assert Path(linux.prepare_python(selected)) == build_python


def test_missing_venv_support_is_explained(build_modules, monkeypatch, tmp_path):
    checks, linux = build_modules
    selected = environment(checks, "/usr/bin/python3", version="3.12.3")
    monkeypatch.setattr(linux, "ROOT", tmp_path)

    def unavailable_venv(command, **kwargs):
        raise linux.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(linux.subprocess, "run", unavailable_venv)
    with pytest.raises(SystemExit) as failure:
        linux.prepare_python(selected)
    assert "python3-venv" in str(failure.value)
    assert "ensurepip" in str(failure.value)


def test_main_keeps_dependency_install_and_build_in_selected_venv(
    build_modules, monkeypatch, tmp_path
):
    checks, linux = build_modules
    selected = environment(checks, "/usr/bin/python3", version="3.12.3")
    prepared_python = str(tmp_path / ".venv-build-linux" / "bin" / "python")
    commands = []
    monkeypatch.setattr(linux.sys, "platform", "linux")
    monkeypatch.setattr(linux, "ROOT", tmp_path)
    monkeypatch.setenv("PYTHON", selected.executable)

    def select(requested):
        assert requested == selected.executable
        return selected

    def prepare(candidate):
        assert candidate == selected
        return prepared_python

    def run(command, **kwargs):
        assert kwargs["check"] is True
        assert kwargs["cwd"] == tmp_path
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(linux, "select_python", select)
    monkeypatch.setattr(linux, "prepare_python", prepare)
    monkeypatch.setattr(linux.subprocess, "run", run)
    assert linux.main() == 0
    assert [command[0] for command in commands] == [prepared_python] * 3
    assert commands[0][1:4] == ["-m", "pip", "install"]
    assert commands[1][1:] == ["-m", "pytest"]
    assert commands[2][1:] == ["scripts/build.py"]
