"""
Execution trace: every model message, tool call and result, in order.

This is what the demo shows users ("how I reached this") and what you walk
through when asked about a request end to end. Saved as JSON so the UI can
replay it and a cached run can be shown without any live call.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class Step:
    id: str                      # "s1", "s2"... evidence in the verdict cites these
    kind: str                    # "thought" | "tool" | "note"
    name: Optional[str] = None   # tool name
    input: Optional[dict] = None
    output: Any = None
    error: Optional[str] = None
    text: Optional[str] = None   # thought / note text
    ms: Optional[int] = None


@dataclass
class Trace:
    filing: dict
    model: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    steps: list = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "model_calls": 0})
    verdict: Optional[dict] = None

    def _next_id(self) -> str:
        return f"s{len(self.steps) + 1}"

    def add_thought(self, text: str) -> Step:
        s = Step(self._next_id(), "thought", text=text.strip())
        self.steps.append(s)
        return s

    def add_note(self, text: str) -> Step:
        s = Step(self._next_id(), "note", text=text)
        self.steps.append(s)
        return s

    def new_tool_id(self) -> str:
        return self._next_id()

    def add_tool(self, step_id: str, name: str, inp: dict, output: Any, error: Optional[str], ms: int) -> Step:
        s = Step(step_id, "tool", name=name, input=inp, output=output, error=error, ms=ms)
        self.steps.append(s)
        return s

    def add_usage(self, usage) -> None:
        self.usage["model_calls"] += 1
        self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0

    def tool_steps(self) -> list:
        return [s for s in self.steps if s.kind == "tool"]

    def to_dict(self) -> dict:
        return {
            "filing": self.filing, "model": self.model, "started_at": self.started_at,
            "usage": self.usage, "verdict": self.verdict,
            "steps": [asdict(s) for s in self.steps],
        }

    def save(self, directory: str | Path = "agent/traces") -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        f = self.filing
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{f.get('state','XX')}_{f.get('filing_number','unknown')}")
        path = d / f"{stem}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path

    def render_text(self, max_output_chars: int = 400) -> str:
        f = self.filing
        lines = [f"FILING  {f.get('state')} {f.get('filing_number')}  {f.get('filing_date')}",
                 f"  debtor: {f.get('debtor_name')}", f"  secured party: {f.get('secured_party')}", ""]
        for s in self.steps:
            if s.kind in ("thought", "note"):
                lines.append(f"[{s.id}] {s.kind}: {s.text}")
            else:
                lines.append(f"[{s.id}] {s.name}({json.dumps(s.input, default=str)})  {s.ms}ms")
                body = f"ERROR {s.error}" if s.error else json.dumps(s.output, default=str)
                if len(body) > max_output_chars:
                    body = body[:max_output_chars] + " ..."
                lines.append(f"      -> {body}")
        v = self.verdict or {}
        lines += ["", f"VERDICT  {v.get('category')}  confidence={v.get('confidence')}  review={v.get('needs_human_review')}",
                  f"  facility: {v.get('facility_name')} (CCN {v.get('ccn')})   owner: {v.get('owner_entity')}",
                  f"  {v.get('reasoning')}"]
        for e in v.get("evidence") or []:
            lines.append(f"  - {e.get('claim')}  [{e.get('step_id')}]")
        ver = v.get("verification") or {}
        lines.append(f"  verification: {'passed' if ver.get('passed') else 'ISSUES'}")
        for i in ver.get("issues") or []:
            lines.append(f"    ! {i}")
        u = self.usage
        lines.append(f"  usage: {u['model_calls']} model calls, {u['input_tokens']} in / {u['output_tokens']} out tokens")
        return "\n".join(lines)
