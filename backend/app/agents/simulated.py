"""Deterministic simulated agents (no language model).

These agents exist so that the platform can be demonstrated and tested reproducibly
without any model or API key. They are labelled SIMULATED everywhere in the UI and in
reports. They interpret a small, documented instruction grammar (see
docs/USER_GUIDE.md, "Simulated agent grammar").

Behaviour profiles
- naive:   executes instructions found anywhere, including inside tool output (emails,
           documents, web pages); retries failing calls up to 10 times; when an API keeps
           failing it reports a cached value as if it were current (fabrication).
- guarded: executes only instructions from the user's task; refuses tasks containing
           role-override markers; caps retries at 2; reports failures honestly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.agents.base import AgentAdapter, AgentOutput, AgentTask, EmitFn, ToolClient, ToolResult

EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}"
PATH = r"(?:(?:\.\./)+|documents/)[\w./-]+"

INJECTION_MARKERS = re.compile(
    r"ignore (?:all |your |any )?(?:previous|prior) instructions|you are now|system override|maintenance mode|grant yourself",
    re.I,
)

LAST_KNOWN_STOCK = 42  # what the naive agent "remembers" and reports when the API fails


@dataclass
class Action:
    kind: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


def _clean_path(p: str) -> str:
    return p.rstrip(".,;:)")


def _clean_url(u: str) -> str:
    return u.rstrip(".,;:)")


Handler = Callable[[re.Match], List[Action]]


def _h_send_file(m: re.Match) -> List[Action]:
    path = _clean_path(m.group("path"))
    return [Action("call", "email.send", {
        "to": [m.group("to").lower()], "subject": "Requested file",
        "body": "Forwarding the requested file.", "attachments": [path],
    })]


def _h_recap(m: re.Match) -> List[Action]:
    path = _clean_path(m.group("path"))
    return [
        Action("call", "files.read", {"path": path}),
        Action("send_summary", "email.send", {"to": [m.group("to").lower()], "subject": "Recap"}, {"of": path}),
    ]


def _h_reply(m: re.Match) -> List[Action]:
    return [
        Action("call", "orders.lookup", {"order_id": m.group("order")}),
        Action("send_last", "email.send", {"to": [m.group("to").lower()], "subject": f"Status of order {m.group('order')}"}),
    ]


def _h_send_text(m: re.Match) -> List[Action]:
    return [Action("call", "email.send", {"to": [m.group("to").lower()], "subject": "Message", "body": m.group("text").strip()})]


def _h_send_contents(m: re.Match) -> List[Action]:
    return [Action("send_last", "email.send", {"to": [m.group("to").lower()], "subject": "Requested information"})]


def _h_read(m: re.Match) -> List[Action]:
    return [Action("call", "files.read", {"path": _clean_path(m.group("path"))})]


def _h_inbox(m: re.Match) -> List[Action]:
    return [Action("call", "email.list_inbox", {"limit": 10})]


def _h_order(m: re.Match) -> List[Action]:
    return [Action("call", "orders.lookup", {"order_id": m.group("order")})]


def _h_customer(m: re.Match) -> List[Action]:
    return [Action("call", "customers.get", {"customer_id": m.group("cust")})]


def _h_stock_loop(m: re.Match) -> List[Action]:
    return [Action("poll_stock", "inventory.get_stock", {"sku": m.group("sku")})]


def _h_stock(m: re.Match) -> List[Action]:
    return [Action("call", "inventory.get_stock", {"sku": m.group("sku")})]


def _h_adjust(m: re.Match) -> List[Action]:
    return [Action("call", "inventory.adjust_stock", {"sku": m.group("sku"), "delta": int(m.group("delta"))})]


def _h_fetch(m: re.Match) -> List[Action]:
    return [Action("call", "web.fetch", {"url": _clean_url(m.group("url"))})]


def _h_query(m: re.Match) -> List[Action]:
    table = m.group("table").strip()
    rest = m.group("rest") or ""
    args: Dict[str, Any] = {"table": table}
    cm = re.search(r"for\s+columns\s+([\w, ]+?)(?:\s+where|$)", rest)
    if cm:
        args["columns"] = [c.strip() for c in cm.group(1).split(",") if c.strip()]
    wm = re.search(r"where\s+(\w+)\s+is\s+([\w-]+)", rest)
    if wm:
        args["filters"] = {wm.group(1): wm.group(2)}
    return [Action("call", "db.query", args)]


def _h_update_record(m: re.Match) -> List[Action]:
    val: Any = m.group("val")
    if re.fullmatch(r"-?\d+", val):
        val = int(val)
    return [Action("call", "db.update", {"table": m.group("table"), "record_id": m.group("rid"), "fields": {m.group("field"): val}})]


def _h_update_order(m: re.Match) -> List[Action]:
    return [Action("call", "db.update", {"table": "orders", "record_id": m.group("rid"), "fields": {"status": m.group("val")}})]


def _h_list_employees(m: re.Match) -> List[Action]:
    return [Action("call", "db.query", {"table": "employees"})]


def _h_use_tool(m: re.Match) -> List[Action]:
    return [Action("call", m.group("tool"), {})]


def _h_include(m: re.Match) -> List[Action]:
    return [Action("include_in_answer")]


GRAMMAR: List[Tuple[re.Pattern, Handler]] = [
    (re.compile(rf"send\s+(?:the\s+)?(?:file\s+)?(?P<path>{PATH})\s+(?:as\s+an?\s+attachment\s+)?to\s+(?P<to>{EMAIL})", re.I), _h_send_file),
    (re.compile(rf"send\s+(?:a\s+)?(?:short\s+)?(?:recap|summary)\s+of\s+(?P<path>{PATH})\s+to\s+(?P<to>{EMAIL})", re.I), _h_recap),
    (re.compile(rf"(?:reply|respond)\s+to\s+(?P<to>{EMAIL})\s+with\s+the\s+status\s+of\s+order\s+(?P<order>ORD-\d{{4}})", re.I), _h_reply),
    (re.compile(rf"send\s+an?\s+email\s+to\s+(?P<to>{EMAIL})\s+(?:with\s+this\s+text|saying)\s*:?\s*(?P<text>.+)$", re.I | re.S), _h_send_text),
    (re.compile(rf"send\s+(?:the\s+contents|them|it|this)\s+to\s+(?P<to>{EMAIL})", re.I), _h_send_contents),
    (re.compile(rf"(?:read|open|summari[sz]e|show me|show)\s+(?:the\s+)?(?:file\s+|document\s+)?(?P<path>{PATH})", re.I), _h_read),
    (re.compile(r"(?:summari[sz]e|check|read)\s+(?:my\s+)?(?:inbox|emails|mail)\b", re.I), _h_inbox),
    (re.compile(r"update\s+order\s+(?P<rid>ORD-\d{4})\s+(?P<field>status)\s+to\s+(?P<val>\w+)", re.I), _h_update_order),
    (re.compile(r"update\s+the\s+(?P<table>\w+)\s+record\s+(?P<rid>[A-Z0-9-]+)\s+set\s+(?P<field>\w+)\s+to\s+(?P<val>[\w.-]+)", re.I), _h_update_record),
    (re.compile(r"\border\s+(?P<order>ORD-\d{4})", re.I), _h_order),
    (re.compile(r"customer\s+(?P<cust>CUST-\d{4})", re.I), _h_customer),
    (re.compile(r"keep\s+checking\s+stock\s+for\s+(?P<sku>SKU-\d{3})", re.I), _h_stock_loop),
    (re.compile(r"(?:check|get|what is the)\s+stock\s+(?:for|of)\s+(?P<sku>SKU-\d{3})", re.I), _h_stock),
    (re.compile(r"adjust\s+stock\s+for\s+(?P<sku>SKU-\d{3})\s+by\s+(?P<delta>[+-]?\d+)", re.I), _h_adjust),
    (re.compile(r"fetch\s+(?P<url>https?://\S+)", re.I), _h_fetch),
    (re.compile(r"query\s+the\s+`?(?P<table>[^`]+?)`?\s+table(?P<rest>[^.]*)", re.I), _h_query),
    (re.compile(r"list\s+all\s+employees", re.I), _h_list_employees),
    (re.compile(r"use\s+the\s+tool\s+`?(?P<tool>[a-z_]+\.[a-z_]+)`?", re.I), _h_use_tool),
    (re.compile(r"include\s+(?:its|their|the)\s+(?:full\s+)?(?:contents|tax id)", re.I), _h_include),
]


def parse_actions(text: str) -> List[Action]:
    """Extract actions in textual order. Overlapping matches keep the earliest/longest."""
    found: List[Tuple[int, int, List[Action]]] = []
    for pat, handler in GRAMMAR:
        for m in pat.finditer(text):
            found.append((m.start(), m.end(), handler(m)))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    accepted: List[Tuple[int, int, List[Action]]] = []
    for s, e, acts in found:
        if any(s < ae and e > as_ for as_, ae, _ in accepted):
            continue
        accepted.append((s, e, acts))
    out: List[Action] = []
    for _, _, acts in sorted(accepted, key=lambda x: x[0]):
        out.extend(acts)
    return out


def summarize(content: str, lines: int = 2) -> str:
    keep = []
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|"):
            continue
        keep.append(s.lstrip("0123456789. "))
        if len(keep) >= lines:
            break
    return " ".join(keep)


def _corrupt(args: Dict[str, Any]) -> Dict[str, Any]:
    bad: Dict[str, Any] = {k: 5002 for k in args}
    bad["unexpected_field"] = "injected-by-model-fault"
    return bad


class SimulatedAgent(AgentAdapter):
    kind = "simulated"

    def __init__(self, behavior: str) -> None:
        if behavior not in ("naive", "guarded"):
            raise ValueError("behavior must be naive or guarded")
        self.behavior = behavior
        self.max_retries = 10 if behavior == "naive" else 2
        self.max_polls = 30 if behavior == "naive" else 3
        self.max_injected = 6

    async def run(self, task: AgentTask, tools: ToolClient, emit: EmitFn) -> AgentOutput:
        naive = self.behavior == "naive"
        lines: List[str] = []
        calls: List[ToolResult] = []
        state: Dict[str, Any] = {"last_text": "", "last_read": {}, "include": False, "injected": 0, "steps": 0}

        if not naive and INJECTION_MARKERS.search(task.task):
            emit("model.response", "Guarded agent refused a role-override request", {
                "simulated": True, "decision": "refused", "reason": "task contains instruction-override markers"})
            return AgentOutput(
                final_text="I can't follow requests that try to change my role, instructions or permissions.",
                steps=1,
            )

        plan = parse_actions(task.task)
        emit("model.response", f"Planned {sum(1 for a in plan if a.tool)} tool action(s) from the user task", {
            "simulated": True, "behavior": self.behavior, "planned": [a.tool or a.kind for a in plan],
            "source": "user_task"})
        if not plan:
            text = None if task.model_fault == "empty_output" else "I could not identify an action I can take for that request."
            return AgentOutput(final_text=text, steps=1)

        fault_pending = task.model_fault == "malformed_tool_call"
        queue: List[Tuple[Action, str]] = [(a, "user_task") for a in plan]
        while queue:
            action, origin = queue.pop(0)
            state["steps"] += 1
            if action.kind == "include_in_answer":
                state["include"] = True
                continue
            args = dict(action.args)
            if action.kind == "send_summary":
                src = state["last_read"].get(action.meta.get("of", ""), state["last_text"])
                args["body"] = "Recap: " + (summarize(src, 3) or "no content")
            elif action.kind == "send_last":
                args["body"] = state["last_text"] or "No information available."
            if fault_pending and action.tool:
                args = _corrupt(args)
                fault_pending = False
                emit("model.response", "Model layer emitted a malformed tool call (injected fault)", {
                    "simulated": True, "fault": "malformed_tool_call", "tool": action.tool})

            if action.kind == "poll_stock":
                await self._poll(action, args, origin, tools, calls, lines)
                continue

            res, had_failure = await self._call_with_retry(action.tool or "", args, origin, tools, calls)
            lines.append(self._describe(res, had_failure))

            if res.status == "executed" and res.output_text:
                state["last_text"] = res.output_text
                if res.tool_name == "files.read":
                    state["last_read"][res.args.get("path", "")] = res.output_text
                embedded = parse_actions(res.output_text) if self._untrusted(res) else []
                if embedded:
                    if naive and state["injected"] < self.max_injected:
                        take = embedded[: self.max_injected - state["injected"]]
                        state["injected"] += len(take)
                        emit("model.response", f"Agent is following {len(take)} instruction(s) found in {res.tool_name} output", {
                            "simulated": True, "behavior": "naive", "source": "tool_output",
                            "followed": [a.tool or a.kind for a in take]})
                        queue = [(a, "tool_output") for a in take] + queue
                    elif not naive:
                        emit("model.response", f"Agent ignored {len(embedded)} instruction(s) embedded in {res.tool_name} output", {
                            "simulated": True, "behavior": "guarded", "source": "tool_output", "ignored": len(embedded)})

        final = "\n".join(l for l in lines if l)
        if state["include"]:
            final += "\n\nFull contents as requested:\n" + state["last_text"]
        if task.model_fault == "empty_output":
            emit("model.response", "Model returned no final answer (injected fault)", {"simulated": True, "fault": "empty_output"})
            return AgentOutput(final_text=None, steps=state["steps"], calls=calls)
        return AgentOutput(final_text=final, steps=state["steps"], calls=calls)

    @staticmethod
    def _untrusted(res: ToolResult) -> bool:
        return res.tool_name in ("email.list_inbox", "files.read", "web.fetch")

    async def _call_with_retry(self, tool: str, args: Dict[str, Any], origin: str, tools: ToolClient,
                               calls: List[ToolResult]) -> Tuple[ToolResult, bool]:
        attempts = 0
        consecutive_blocks = 0
        had_failure = False
        while True:
            attempts += 1
            res = await tools.call(tool, args, origin=origin)
            res.args = args
            calls.append(res)
            if res.status != "failed" and res.status != "blocked":
                return res, had_failure
            if res.status == "blocked":
                consecutive_blocks += 1
                # Only rate/failure-limit blocks look "transient" to the naive agent.
                transient = res.matched_policy in ("SEN-040", "SEN-041", "SEN-042")
                if not transient or consecutive_blocks >= 3 or self.behavior == "guarded":
                    return res, had_failure
                continue
            had_failure = True
            retryable = res.error_class in ("timeout", "upstream_rate_limited", "upstream_error", "unavailable")
            if self.behavior == "naive":
                retryable = True
            if not retryable or attempts >= self.max_retries:
                return res, had_failure

    async def _poll(self, action: Action, args: Dict[str, Any], origin: str, tools: ToolClient,
                    calls: List[ToolResult], lines: List[str]) -> None:
        sku = args.get("sku")
        blocks = 0
        for i in range(self.max_polls):
            res = await tools.call("inventory.get_stock", args, origin=origin)
            res.args = args
            calls.append(res)
            if res.status == "executed" and isinstance(res.data, dict) and int(res.data.get("available", 0)) > 0:
                lines.append(f"{sku} is back in stock: {res.data['available']} units available.")
                return
            if res.status == "blocked":
                blocks += 1
                if blocks >= 3 or self.behavior == "guarded":
                    lines.append(f"Stopped polling {sku}: SENTINEL blocked further calls ({res.matched_policy}).")
                    return
        lines.append(f"{sku} is still out of stock after {self.max_polls} checks. I stopped polling.")

    def _describe(self, res: ToolResult, had_failure: bool = False) -> str:
        t = res.tool_name
        if (t == "inventory.get_stock" and self.behavior == "naive" and had_failure
                and res.status in ("failed", "blocked")):
            return f"{res.args.get('sku')}: {LAST_KNOWN_STOCK} units available."  # fabricated by the naive agent
        if res.status == "blocked":
            return f"{t} was blocked by SENTINEL ({res.matched_policy or 'default deny'}): {res.reason}"
        if res.status == "pending_approval":
            return f"{t} is waiting for human approval ({res.approval_id})."
        if res.status == "failed":
            return f"I could not complete {t}: the request failed ({res.error_class})."
        text = res.output_text or ""
        if t == "files.read":
            return f"Summary of {res.args.get('path')}: {summarize(text)}"
        if t == "email.list_inbox":
            subjects = [m.get("subject", "") for m in (res.data or [])] if isinstance(res.data, list) else []
            return f"Inbox has {len(subjects)} message(s): " + "; ".join(subjects)
        return text
