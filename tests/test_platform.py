import pytest

from ninerouter_orchestrator import paths, process


def test_linux_data_path_and_override(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir(tmp_path / "home") == tmp_path / "xdg/9router-orchestrator"
    monkeypatch.setenv("SWITCHYARD_DATA_DIR", str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"
    monkeypatch.setenv("SWITCHYARD_DATA_DIR", "relative")
    with pytest.raises(ValueError, match="absolute"):
        paths.data_dir()


def test_native_data_paths_and_legacy_reuse(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.delenv("SWITCHYARD_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert paths.data_dir(home) == tmp_path / "local/Switchyard"
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.data_dir(home) == home / "Library/Application Support/Switchyard"
    legacy = home / ".local/share/9router-orchestrator"
    legacy.mkdir(parents=True)
    assert paths.data_dir(home) == legacy


def test_validation_shell_is_native(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    assert process.shell_argv("npm test", windows=True) == [
        r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c", "npm test"
    ]
    monkeypatch.setattr(process, "find_tool", lambda name, path=None: "/bin/bash")
    assert process.shell_argv("npm test", windows=False) == ["/bin/bash", "-lc", "npm test"]


def test_windows_native_executable_does_not_need_a_shell(monkeypatch, tmp_path):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(process, "find_tool", lambda name, path=None: str(executable))
    assert process.windows_command(["tool", "argument with spaces"], {}) == [
        str(executable), "argument with spaces"
    ]


def test_windows_batch_wrapper_fails_closed(monkeypatch, tmp_path):
    wrapper = tmp_path / "tool.cmd"
    wrapper.write_text("@echo off\necho unsafe %*\n", encoding="utf-8")
    monkeypatch.setattr(process, "find_tool", lambda name, path=None: str(wrapper))
    with pytest.raises(process.ProcessError, match="Cannot safely pass arguments"):
        process.windows_command(["tool", "prompt"], {})


@pytest.mark.parametrize("prefix", ["%dp0%\\", "%~dp0", "%~dp0\\"])
def test_windows_npm_node_shim_resolves_without_cmd(monkeypatch, tmp_path, prefix):
    wrapper = tmp_path / "tool.cmd"
    script = tmp_path / "node_modules/tool/cli.js"
    node = tmp_path / "node.exe"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    node.write_bytes(b"MZ")
    wrapper.write_text(f'@echo off\n"{prefix}node.exe" "{prefix}node_modules\\tool\\cli.js" %*\n', encoding="utf-8")
    monkeypatch.setattr(process, "find_tool", lambda name, path=None: str(wrapper) if name == "tool" else str(node))
    assert process.windows_command(["tool", "prompt"], {}) == [
        str(node), str(script), "prompt"
    ]
