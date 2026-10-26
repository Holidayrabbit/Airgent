from __future__ import annotations

from types import SimpleNamespace

from app.agents.runner import AgentRunnerService
from app.kernel.events import PiEvent
from app.tui import AirgentTUI, ExecutionEntry
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType


def _runner_service() -> AgentRunnerService:
    return object.__new__(AgentRunnerService)


def _fake_services(tmp_path):
    store = SimpleNamespace(
        list_sessions=lambda limit=30: [],
        get_messages=lambda session_id: [],
        list_memories=lambda limit=5: [],
    )
    settings = SimpleNamespace(project_root=tmp_path)
    return SimpleNamespace(settings=settings, store=store, runner=SimpleNamespace())


def _render_text(fragments) -> str:
    return "".join(text for _, text in fragments)








def test_pi_tool_event_translates_to_readable_summary() -> None:
    service = _runner_service()

    event = service._translate(
        PiEvent("tool_start", {"name": "read", "arguments": {"path": "README.md"}}),
        session_id="s1",
    )

    assert event.kind == "tool"
    assert event.summary == "Reading README.md"
    assert event.detail == "tool: read\npath: README.md"


def test_pi_tool_end_reports_failure() -> None:
    service = _runner_service()

    event = service._translate(
        PiEvent("tool_end", {"name": "bash", "is_error": True, "text": "boom"}), session_id="s1"
    )

    assert event.kind == "tool_output"
    assert event.summary == "Tool step failed"


def test_pi_memory_tools_keep_native_summaries() -> None:
    service = _runner_service()

    search = service._translate(
        PiEvent("tool_start", {"name": "search_memory", "arguments": {"query": "uv"}}), session_id="s1"
    )
    remember = service._translate(PiEvent("tool_start", {"name": "remember_note", "arguments": {}}), session_id="s1")

    assert search.summary == "Searching memory for 'uv'"
    assert remember.summary == "Saving a memory"


def test_chat_renders_execution_in_main_thread(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.state.messages = [("user", "查看你工作目录的readme")]
    tui.state.busy = True
    tui.state.execution_log = [
        ExecutionEntry(kind="tool", summary="Reading README.md", detail="tool: read_file\npath: README.md"),
        ExecutionEntry(kind="message", summary="Drafting the response", detail=""),
    ]

    text = _render_text(tui._render_chat())

    assert "Process" not in text
    assert "Execution Detail" not in text
    assert "You\n查看你工作目录的readme" in text
    assert "Airgent\nWorking" in text
    assert "· Reading README.md" in text
    assert "· Drafting the response" in text


def test_chat_source_lines_keep_full_transcript(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.state.messages = [("user", f"message {i}") for i in range(20)]

    source_lines = tui._chat_source_lines()
    rendered_text = "\n".join(line.text for line in source_lines)

    assert "message 0" in rendered_text
    assert "message 19" in rendered_text


def test_chat_scroll_follows_latest_render_height(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(36)))]
    tui.state.follow_latest_chat = True
    tui.state.chat_scroll = 0

    tui._render_chat()

    assert tui.state.chat_scroll == 28


def test_chat_scroll_can_browse_up_and_return_to_latest(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui._invalidate = lambda: None
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(30)))]
    tui.state.chat_scroll = 22
    tui.state.follow_latest_chat = True

    tui._scroll_chat(-5)

    assert tui.state.chat_scroll == 17
    assert tui.state.follow_latest_chat is False

    tui._scroll_chat_to(tui._chat_max_scroll(), follow_latest=True)

    assert tui.state.chat_scroll == 22
    assert tui.state.follow_latest_chat is True


def test_after_render_pins_chat_to_bottom_when_following_latest(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(42)))]
    tui.state.chat_scroll = 0
    tui.state.follow_latest_chat = True

    invalidated = {"count": 0}

    class FakeApp:
        def invalidate(self) -> None:
            invalidated["count"] += 1

    tui._after_render(FakeApp())

    assert tui.state.chat_scroll == 34
    assert invalidated["count"] == 1


def test_after_render_clamps_manual_scroll_when_height_shrinks(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(18)))]
    tui.state.chat_scroll = 20
    tui.state.follow_latest_chat = False

    invalidated = {"count": 0}

    class FakeApp:
        def invalidate(self) -> None:
            invalidated["count"] += 1

    tui._after_render(FakeApp())

    assert tui.state.chat_scroll == 10
    assert invalidated["count"] == 1


def test_mouse_scroll_moves_chat_viewport(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant")
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui._invalidate = lambda: None
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(30)))]
    tui.state.chat_scroll = 22
    tui.state.follow_latest_chat = True

    tui._handle_chat_mouse(
        MouseEvent(position=SimpleNamespace(x=0, y=0), event_type=MouseEventType.SCROLL_UP, button=MouseButton.LEFT, modifiers=())
    )

    assert tui.state.chat_scroll == 20
    assert tui.state.follow_latest_chat is False

    tui._handle_chat_mouse(
        MouseEvent(position=SimpleNamespace(x=0, y=0), event_type=MouseEventType.SCROLL_DOWN, button=MouseButton.LEFT, modifiers=())
    )

    assert tui.state.chat_scroll == 22
