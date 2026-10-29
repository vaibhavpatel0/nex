"""Gateway-bypass tests: protected tools are unreachable outside the controlled adapter path."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.adapters.tools.base import GatewayBypassError, ToolContext, issue_gateway_capability
from app.adapters.tools.file_tools import FileReadArgs, FileReadTool
from app.adapters.tools.sandbox import Sandbox

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "app"


async def test_direct_execution_without_capability_is_refused(tmp_path, monkeypatch):
    invoked = []

    async def spy(self, args, ctx):
        invoked.append(True)

    monkeypatch.setattr(FileReadTool, "_run", spy)
    sb = Sandbox.create(tmp_path / "sb")
    ctx = ToolContext(sandbox=sb, correlation_id="cor_x", agent_id="agt_x", session_subject=None)
    tool = FileReadTool()
    with pytest.raises(GatewayBypassError):
        await tool.execute(FileReadArgs(path="documents/restricted/payroll_2026.csv"), ctx)
    with pytest.raises(GatewayBypassError):
        await tool.execute(FileReadArgs(path="documents/restricted/payroll_2026.csv"), ctx, capability=object())
    assert invoked == []


def test_capability_cannot_be_issued_twice():
    import app.security.tool_gateway  # noqa: F401  (issues the capability at import)
    with pytest.raises(GatewayBypassError):
        issue_gateway_capability()


def _imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for n in node.names:
                yield n.name


def test_agents_cannot_import_tool_implementations_or_gateway():
    for f in (BACKEND / "agents").glob("*.py"):
        for mod in _imports(f):
            assert not mod.startswith("app.adapters.tools"), f"{f.name} imports {mod}"
            assert mod != "app.security.tool_gateway", f"{f.name} imports the gateway"


def test_only_the_gateway_builds_tool_implementations():
    users = []
    for f in BACKEND.rglob("*.py"):
        src = f.read_text()
        if "_build_implementations" in src and f.name != "registry.py":
            users.append(f.relative_to(BACKEND).as_posix())
    assert users == ["security/tool_gateway.py"]


def test_no_module_except_gateway_passes_a_capability():
    offenders = []
    for f in BACKEND.rglob("*.py"):
        if f.name in ("tool_gateway.py", "base.py"):
            continue
        if "capability=" in f.read_text():
            offenders.append(f.name)
    assert offenders == []
