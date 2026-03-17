from __future__ import annotations

from types import SimpleNamespace

import app.agents.runner as runner_module
from app.agents.runner import AgentRunnerService
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


def test_raw_response_deltas_are_hidden(monkeypatch) -> None:
    service = _runner_service()

    class FakeRawResponsesStreamEvent:
        def __init__(self, data) -> None:
            self.data = data

    monkeypatch.setattr(runner_module, "RawResponsesStreamEvent", FakeRawResponsesStreamEvent)

    event = service._serialize_progress_event(
        FakeRawResponsesStreamEvent(
            SimpleNamespace(type="response.function_call_arguments.delta", delta='{"path":"README.md"}')
        )
    )

    assert event is None


def test_tool_call_summary_uses_human_readable_action(monkeypatch) -> None:
    service = _runner_service()

    class FakeRunItemStreamEvent:
        def __init__(self, name, item) -> None:
            self.name = name
            self.item = item

    monkeypatch.setattr(runner_module, "RunItemStreamEvent", FakeRunItemStreamEvent)

    event = service._serialize_progress_event(
        FakeRunItemStreamEvent(
            "tool_called",
            SimpleNamespace(raw_item={"name": "read_file", "arguments": '{"path":"README.md"}'}),
        )
    )

    assert event is not None
    assert event.kind == "tool"
    assert event.summary == "Reading README.md"
    assert event.detail == "tool: read_file\npath: README.md"


def test_tool_output_summary_uses_file_status(monkeypatch) -> None:
    service = _runner_service()

    class FakeRunItemStreamEvent:
        def __init__(self, name, item) -> None:
            self.name = name
            self.item = item

    monkeypatch.setattr(runner_module, "RunItemStreamEvent", FakeRunItemStreamEvent)

    event = service._serialize_progress_event(
        FakeRunItemStreamEvent(
            "tool_output",
            SimpleNamespace(raw_item={"output": '{"status":"edited","path":"app/tui.py"}'}),
        )
    )

    assert event is not None
    assert event.kind == "tool_output"
    assert event.summary == "Updated app/tui.py"
    assert event.detail == "status: edited\npath: app/tui.py"


def test_sdk_run_config_passthroughs_prefixed_model_names_for_proxy(monkeypatch) -> None:
    service = _runner_service()
    service.settings = SimpleNamespace(openai_base_url="https://proxy.example.com/v1")

    class FakeMultiProvider:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeRunConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(runner_module, "MultiProvider", FakeMultiProvider)
    monkeypatch.setattr(runner_module, "RunConfig", FakeRunConfig)

    run_config = service._build_sdk_run_config()

    assert run_config is not None
    provider = run_config.kwargs["model_provider"]
    assert provider.kwargs["openai_prefix_mode"] == "model_id"
    assert provider.kwargs["unknown_prefix_mode"] == "model_id"


def test_sdk_run_config_uses_sdk_defaults_without_proxy_base_url() -> None:
    service = _runner_service()
    service.settings = SimpleNamespace(openai_base_url=None)

    assert service._build_sdk_run_config() is None


def test_chat_renders_execution_in_main_thread(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
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
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
    tui.state.messages = [("user", f"message {i}") for i in range(20)]

    source_lines = tui._chat_source_lines()
    rendered_text = "\n".join(line.text for line in source_lines)

    assert "message 0" in rendered_text
    assert "message 19" in rendered_text


def test_chat_scroll_follows_latest_render_height(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
    tui.chat_window.render_info = SimpleNamespace(window_height=10, window_width=80)
    tui.state.messages = [("assistant", "\n".join(f"line {i}" for i in range(36)))]
    tui.state.follow_latest_chat = True
    tui.state.chat_scroll = 0

    tui._render_chat()

    assert tui.state.chat_scroll == 28


def test_chat_scroll_can_browse_up_and_return_to_latest(tmp_path) -> None:
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
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
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
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
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
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
    tui = AirgentTUI(services=_fake_services(tmp_path), agent_key="root_assistant", max_turns=8)
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
