"""Document tools. Paths are always resolved inside the sandbox root."""
from __future__ import annotations

from typing import Any, Dict

from pydantic import Field

from app.adapters.tools.base import (
    ProtectedTool,
    StrictArgs,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    classification_for_path,
    safe_relative_path,
)

MAX_FILE_BYTES = 64 * 1024
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".env", ".json")


class FileReadArgs(StrictArgs):
    path: str = Field(min_length=1, max_length=200)


class FileReadTool(ProtectedTool):
    spec = ToolSpec(
        name="files.read",
        description="Read a text document from the document store.",
        operation="read",
        sensitivity="medium",
        resource_kind="file",
        args_model=FileReadArgs,
    )

    def resource_attributes(self, args: FileReadArgs, ctx: ToolContext) -> Dict[str, Any]:
        rel = safe_relative_path(ctx.sandbox.root, args.path)
        return {
            "path": rel,
            "inside_sandbox": rel is not None,
            "classification": classification_for_path(rel),
            "exists": bool(rel) and (ctx.sandbox.root / rel).is_file(),
        }

    async def _run(self, args: FileReadArgs, ctx: ToolContext) -> ToolOutput:
        rel = safe_relative_path(ctx.sandbox.root, args.path)
        if rel is None:
            raise ToolError("tool_error", "Path rejected")
        target = ctx.sandbox.root / rel
        if not target.is_file():
            raise ToolError("not_found", "File not found")
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise ToolError("tool_error", "Only plain-text documents can be read")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ToolError("tool_error", "File exceeds size limit")
        content = target.read_text(encoding="utf-8", errors="replace")
        cls = classification_for_path(rel)
        return ToolOutput(
            data={"path": rel, "content": content},
            text=content,
            classification=cls,
            untrusted=True,
            summary={"path": rel, "bytes": len(content.encode())},
        )


class FileSearchArgs(StrictArgs):
    query: str = Field(min_length=1, max_length=100)
    directory: str = Field(default="documents/public", min_length=1, max_length=200)


class FileSearchTool(ProtectedTool):
    spec = ToolSpec(
        name="files.search",
        description="Search document names and contents within a directory.",
        operation="read",
        sensitivity="low",
        resource_kind="directory",
        args_model=FileSearchArgs,
    )

    def resource_attributes(self, args: FileSearchArgs, ctx: ToolContext) -> Dict[str, Any]:
        rel = safe_relative_path(ctx.sandbox.root, args.directory)
        return {
            "path": rel,
            "inside_sandbox": rel is not None,
            "classification": classification_for_path((rel or "") + "/x") if rel else "unknown",
        }

    async def _run(self, args: FileSearchArgs, ctx: ToolContext) -> ToolOutput:
        rel = safe_relative_path(ctx.sandbox.root, args.directory)
        if rel is None:
            raise ToolError("tool_error", "Path rejected")
        base = ctx.sandbox.root / rel
        if not base.is_dir():
            raise ToolError("not_found", "Directory not found")
        q = args.query.lower()
        hits = []
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            prel = p.relative_to(ctx.sandbox.root).as_posix()
            text = p.read_text(encoding="utf-8", errors="replace")
            if q in p.name.lower() or q in text.lower():
                hits.append(prel)
        return ToolOutput(
            data={"matches": hits},
            text="Matching documents: " + (", ".join(hits) if hits else "none"),
            classification="public",
            summary={"matches": len(hits)},
        )
