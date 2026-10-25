from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.kernel.events import assistant_text_from_messages, parse_event
from app.kernel.setup import PROXY_PROVIDER_ID, prepare_environment
from app.memory.store import LocalStore

# Records below were captured from a real `pi --mode rpc` run (pi 0.74.x).

TEXT_DELTA = {
    "type": "message_update",
    "assistantMessageEvent": {"type": "text_delta", "delta": "banana DONE"},
}

TOOLCALL_START = {
    "type": "message_update",
    "assistantMessageEvent": {
        "type": "toolcall_start",
        "contentIndex": 0,
        "partial": {
            "role": "assistant",
            "content": [
                {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {"path": "note.txt"}}
            ],
        },
    },
}

TOOLCALL_END = {
    "type": "message_update",
    "assistantMessageEvent": {
        "type": "toolcall_end",
        "contentIndex": 0,
        "toolCall": {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {"path": "note.txt"}},
    },
}

TOOL_EXEC_START = {
    "type": "tool_execution_start",
    "toolCallId": "call_1",
    "toolName": "read",
    "args": {"path": "note.txt"},
}

TOOL_EXEC_END = {
    "type": "tool_execution_end",
    "toolCallId": "call_1",
    "toolName": "read",
    "result": {"content": [{"type": "text", "text": "banana\n"}]},
    "isError": False,
}

ASSISTANT_ERROR_END = {
    "type": "message_end",
    "message": {
        "role": "assistant",
        "content": [],
        "stopReason": "error",
        "errorMessage": "401 Invalid token",
    },
}

AGENT_END = {
    "type": "agent_end",
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "banana DONE"}],
            "stopReason": "stop",
        },
    ],
}


def test_text_delta_event() -> None:
    event = parse_event(TEXT_DELTA)
    assert event is not None
    assert event.kind == "text_delta"
    assert event.data["delta"] == "banana DONE"


def test_toolcall_events_expose_name_and_arguments() -> None:
    start = parse_event(TOOLCALL_START)
    end = parse_event(TOOLCALL_END)
    assert start is not None and start.kind == "toolcall"
    assert start.data["name"] == "read"
    assert start.data["arguments"] == {"path": "note.txt"}
    assert end is not None and end.kind == "toolcall_end"
    assert end.data["name"] == "read"


def test_tool_execution_events() -> None:
    start = parse_event(TOOL_EXEC_START)
    end = parse_event(TOOL_EXEC_END)
    assert start is not None and start.kind == "tool_start"
    assert start.data["name"] == "read"
    assert end is not None and end.kind == "tool_end"
    assert end.data["text"] == "banana"
    assert end.data["is_error"] is False


def test_assistant_error_surfaces() -> None:
    event = parse_event(ASSISTANT_ERROR_END)
    assert event is not None
    assert event.kind == "assistant_error"
    assert "401" in event.data["error"]


def test_agent_end_extracts_last_assistant_text() -> None:
    assert parse_event(AGENT_END).kind == "agent_end"
    assert assistant_text_from_messages(AGENT_END["messages"]) == "banana DONE"


def test_user_message_end_is_ignored() -> None:
    assert parse_event({"type": "message_end", "message": {"role": "user", "content": []}}) is None


def _settings(tmp_path: Path, *, base_url: str | None) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-test",
        openai_base_url=base_url,
        openai_api_mode="chat_completions",
        default_model="anthropic/claude-sonnet-4.5",
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
    )


def test_prepare_environment_writes_proxy_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path, base_url="https://proxy.example.com/v1")

    environment = prepare_environment(settings)

    assert environment.provider_model == f"{PROXY_PROVIDER_ID}/anthropic/claude-sonnet-4.5"
    models = json.loads((tmp_path / "pi" / "agent" / "models.json").read_text())
    provider = models["providers"][PROXY_PROVIDER_ID]
    assert provider["baseUrl"] == "https://proxy.example.com/v1"
    assert provider["api"] == "openai-completions"
    assert provider["apiKey"] == "sk-test"
    assert provider["models"][0]["id"] == "anthropic/claude-sonnet-4.5"

    addendum = (tmp_path / "pi" / "agent" / "APPEND_SYSTEM.md").read_text()
    assert "search_memory" in addendum

    extension = (tmp_path / "pi" / "agent" / "extensions" / "airgent-memory.ts").read_text()
    assert "__AIRGENT_PYTHON__" not in extension
    assert "search_memory" in extension


def test_prepare_environment_without_proxy_uses_direct_models(tmp_path: Path) -> None:
    settings = _settings(tmp_path, base_url=None)

    environment = prepare_environment(settings)

    assert environment.provider_model is None
    assert not (tmp_path / "pi" / "agent" / "models.json").exists()


def test_kernel_session_mapping_roundtrip(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "store.db")

    assert store.get_kernel_session("s1") is None

    store.set_kernel_session("s1", pi_session_file="/tmp/x/session.jsonl", model="airgent/m")
    mapping = store.get_kernel_session("s1")
    assert mapping == {"pi_session_file": "/tmp/x/session.jsonl", "model": "airgent/m"}

    store.set_kernel_session("s1", pi_session_file="/tmp/y/other.jsonl", model="airgent/m2")
    assert store.get_kernel_session("s1")["pi_session_file"] == "/tmp/y/other.jsonl"

    store.delete_kernel_session("s1")
    assert store.get_kernel_session("s1") is None
