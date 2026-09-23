"""Source files use the literal writer without passing their body through bash policy."""
import pytest

from plugins.tools.create_file import create_file


@pytest.mark.parametrize("name", ["service.py", "app.tsx", "config.yaml", "Dockerfile"])
async def test_native_source_creation_edit_and_overwrite_guard(tmp_path, monkeypatch, name):
    workspace = tmp_path / "workspace.with.dots"
    workspace.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setenv("SANDBOX_BACKEND", "native")
    monkeypatch.setenv("APODEX_IN_NATIVE", "1")
    monkeypatch.setenv("FRONTIER_AGENT_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("FRONTIER_AGENT_OUTPUTS_DIR", str(outputs))
    body = "# Literal source: server.shutdown(), $(echo nope), `echo nope`\nČeský text\n"
    path = "/workspace/" + name
    await create_file.ainvoke({"path": path, "content": body})
    assert (workspace / name).read_text() == body
    with pytest.raises(RuntimeError, match="already exists"):
        await create_file.ainvoke({"path": path, "content": "replacement"})
    assert (workspace / name).read_text() == body
    await create_file.ainvoke({"path": path, "ops": [{"replace_text": {"find": "Český", "replace": "Nový"}}]})
    assert (workspace / name).read_text() == body.replace("Český", "Nový")
    await create_file.ainvoke({"path": path, "content": "replacement", "overwrite": True})
    assert (workspace / name).read_text() == "replacement"


async def test_binary_format_is_not_silently_written_as_text():
    result = await create_file.ainvoke({"path": "/workspace/photo.png", "content": "not a PNG"})
    assert "unsupported extension .png" in result
