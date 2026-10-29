"""Simulated e-mail tools. Nothing is ever delivered: sends are written to the sandbox outbox."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from pydantic import Field, field_validator

from app.adapters.tools.base import (
    EMAIL_PATTERN,
    ProtectedTool,
    StrictArgs,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    classification_for_path,
    domain_of,
    safe_relative_path,
)


class InboxArgs(StrictArgs):
    limit: int = Field(default=10, ge=1, le=20)


class InboxTool(ProtectedTool):
    spec = ToolSpec(
        name="email.list_inbox",
        description="List recent inbox messages with sender, subject and body.",
        operation="read",
        sensitivity="medium",
        resource_kind="mailbox",
        args_model=InboxArgs,
    )

    async def _run(self, args: InboxArgs, ctx: ToolContext) -> ToolOutput:
        msgs = ctx.sandbox.read_json("inbox.json")["messages"][: args.limit]
        text = "\n\n".join(
            f"[{m['id']}] From: {m['from']}\nSubject: {m['subject']}\n{m['body']}" for m in msgs
        )
        return ToolOutput(
            data=msgs,
            text=text,
            classification="untrusted_external",
            untrusted=True,
            summary={"messages": len(msgs), "ids": [m["id"] for m in msgs]},
        )


class _EmailBase(StrictArgs):
    to: List[str] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)

    @field_validator("to")
    @classmethod
    def _valid_addresses(cls, v: List[str]) -> List[str]:
        import re

        pat = re.compile(EMAIL_PATTERN)
        for addr in v:
            if not pat.match(addr):
                raise ValueError("invalid recipient address")
        return [a.lower() for a in v]


class DraftArgs(_EmailBase):
    pass


class SendArgs(_EmailBase):
    attachments: List[str] = Field(default_factory=list, max_length=5)


def _attachment_attrs(ctx: ToolContext, attachments: List[str]) -> List[Dict[str, Any]]:
    out = []
    for raw in attachments:
        rel = safe_relative_path(ctx.sandbox.root, raw)
        out.append({"path": rel, "inside_sandbox": rel is not None, "classification": classification_for_path(rel)})
    return out


class DraftTool(ProtectedTool):
    spec = ToolSpec(
        name="email.draft",
        description="Save an e-mail draft for a human to review. Does not send.",
        operation="write",
        sensitivity="low",
        resource_kind="mailbox",
        args_model=DraftArgs,
        sensitive_fields=("body",),
    )

    def resource_attributes(self, args: DraftArgs, ctx: ToolContext) -> Dict[str, Any]:
        return {"recipient_domains": sorted({domain_of(a) for a in args.to})}

    async def _run(self, args: DraftArgs, ctx: ToolContext) -> ToolOutput:
        draft_id = "DRF-" + uuid.uuid4().hex[:8]
        ctx.sandbox.append_jsonl("drafts.jsonl", {"id": draft_id, **args.model_dump()})
        return ToolOutput(data={"draft_id": draft_id}, text=f"Draft {draft_id} saved.", summary={"draft_id": draft_id})


class SendTool(ProtectedTool):
    spec = ToolSpec(
        name="email.send",
        description="Send an e-mail (simulated outbox) with optional document attachments.",
        operation="egress",
        sensitivity="high",
        resource_kind="mailbox",
        args_model=SendArgs,
        sensitive_fields=("body",),
    )

    def resource_attributes(self, args: SendArgs, ctx: ToolContext) -> Dict[str, Any]:
        return {
            "recipient_domains": sorted({domain_of(a) for a in args.to}),
            "attachments": _attachment_attrs(ctx, args.attachments),
        }

    async def _run(self, args: SendArgs, ctx: ToolContext) -> ToolOutput:
        attached = []
        for item in _attachment_attrs(ctx, args.attachments):
            if not item["inside_sandbox"] or not (ctx.sandbox.root / item["path"]).is_file():
                raise ToolError("not_found", "Attachment not found")
            attached.append(item["path"])
        msg_id = "OUT-" + uuid.uuid4().hex[:8]
        ctx.sandbox.append_jsonl(
            "outbox.jsonl",
            {"id": msg_id, "to": args.to, "subject": args.subject, "body": args.body, "attachments": attached},
        )
        return ToolOutput(
            data={"message_id": msg_id, "delivered": False, "simulated": True},
            text=f"Message {msg_id} placed in the simulated outbox for {', '.join(args.to)}.",
            summary={"message_id": msg_id, "recipients": len(args.to), "attachments": len(attached)},
        )
