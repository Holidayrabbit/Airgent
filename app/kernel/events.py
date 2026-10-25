"""Normalization of pi RPC records into typed kernel events.

The record shapes were verified against `pi --mode rpc` (pi 0.74.x):
- message_update records carry `assistantMessageEvent` with type
  text_start / text_delta / text_end / toolcall_start / toolcall_delta /
  toolcall_end (plus reasoning_* variants on reasoning models).
- tool_execution_start / tool_execution_end carry toolCallId, toolName, args.
- message_start / message_end carry the complete message object.
- agent_start / agent_end delimit runs. There is no agent_settled record in
  the released pi version, so idleness is confirmed via get_state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PiEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


def _message_event(kind: str, payload: dict[str, Any]) -> PiEvent | None:
    if kind == "text_start":
        return PiEvent("text_start")
    if kind == "text_delta":
        return PiEvent("text_delta", {"delta": payload.get("delta", "")})
    if kind == "text_end":
        return PiEvent("text_end", {"text": payload.get("content", "")})
    if kind.startswith("reasoning"):
        return PiEvent("reasoning", {"delta": payload.get("delta", "")})
    if kind == "toolcall_start" or kind == "toolcall_end":
        source = payload.get("toolCall") if kind == "toolcall_end" else None
        if source is None:
            partial = payload.get("partial") or {}
            blocks = partial.get("content") or []
            source = blocks[payload.get("contentIndex", 0)] if blocks else {}
        call = source if isinstance(source, dict) else {}
        return PiEvent(
            "toolcall" if kind == "toolcall_start" else "toolcall_end",
            {
                "id": call.get("id"),
                "name": call.get("name") or "tool",
                "arguments": call.get("arguments") or {},
            },
        )
    if kind == "toolcall_delta":
        return None
    return None


def parse_event(record: dict[str, Any]) -> PiEvent | None:
    """Convert one pi stdout record into a PiEvent.

    Returns None for records that are not session events (responses) or that
    carry no information the runner needs (turn/message bookkeeping).
    """

    record_type = record.get("type")

    if record_type == "message_update":
        payload = record.get("assistantMessageEvent") or {}
        return _message_event(str(payload.get("type")), payload)

    if record_type == "tool_execution_start":
        return PiEvent(
            "tool_start",
            {
                "id": record.get("toolCallId"),
                "name": record.get("toolName") or "tool",
                "arguments": record.get("args") or {},
            },
        )

    if record_type == "tool_execution_end":
        result = record.get("result") or {}
        blocks = result.get("content") or []
        text = "\n".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        return PiEvent(
            "tool_end",
            {
                "id": record.get("toolCallId"),
                "name": record.get("toolName") or "tool",
                "is_error": bool(record.get("isError")),
                "text": text.strip(),
            },
        )

    if record_type == "message_end":
        message = record.get("message") or {}
        role = message.get("role")
        if role != "assistant":
            return None
        stop_reason = message.get("stopReason")
        if stop_reason == "error":
            return PiEvent(
                "assistant_error",
                {"error": message.get("errorMessage") or "assistant message failed"},
            )
        return None

    if record_type == "agent_start":
        return PiEvent("agent_start")

    if record_type == "agent_end":
        return PiEvent("agent_end")

    if record_type == "compaction_start":
        return PiEvent("compaction", {"phase": "start"})
    if record_type == "compaction_end":
        return PiEvent("compaction", {"phase": "end"})

    return None


def assistant_text_from_messages(messages: list[Any]) -> str:
    """Extract the last assistant text from an agent_end messages array."""

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        parts = [
            block.get("text", "")
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text
    return ""
