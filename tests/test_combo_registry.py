from ninerouter_orchestrator.combo_registry import ComboRegistry


def test_refresh_reports_added_and_removed(monkeypatch):
    registry = ComboRegistry()
    snapshots = iter(
        [
            (["OpenCode-Go", "Kimi"], "test"),
            (["Kimi", "Gemini"], "test"),
        ]
    )
    monkeypatch.setattr(registry, "_discover", lambda: next(snapshots))
    first = registry.refresh()
    second = registry.refresh()
    assert first["added"] == ["Kimi", "OpenCode-Go"]
    assert second["added"] == ["Gemini"]
    assert second["removed"] == ["OpenCode-Go"]
    assert second["combos"] == ["Gemini", "Kimi"]


def test_failure_preserves_previous_routes(monkeypatch):
    registry = ComboRegistry()
    registry.combos = ["Kimi"]

    def fail():
        raise OSError("offline")

    monkeypatch.setattr(registry, "_discover", fail)
    result = registry.refresh()
    assert result["combos"] == ["Kimi"]
    assert result["removed"] == []
    assert result["error"]
