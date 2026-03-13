from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import ConditionalProcessor, Processor, Transformation
from prompt_toolkit.styles import Style

from app.api.schemas.agent import AgentRunRequest
from app.agents.runner import AgentProgressEvent
from app.bootstrap import AppServices
from app.memory.store import SessionSummary


class PlaceholderProcessor(Processor):
    def __init__(self, get_placeholder: Callable[[], str]) -> None:
        self.get_placeholder = get_placeholder

    def apply_transformation(self, transformation_input) -> Transformation:
        buffer = transformation_input.buffer_control.buffer
        if buffer.text:
            return Transformation(transformation_input.fragments)
        return Transformation([("class:placeholder", self.get_placeholder())])


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str


@dataclass(frozen=True)
class ExecutionEntry:
    kind: str
    summary: str
    detail: str


@dataclass
class TUIState:
    services: AppServices
    agent_key: str
    max_turns: int | None
    active_session_id: str | None = None
    messages: list[tuple[str, str]] = field(default_factory=list)
    execution_log: list[ExecutionEntry] = field(default_factory=list)
    status: str = "Ready"
    busy: bool = False
    palette_index: int = 0
    detail_open: bool = False
    chat_scroll: int = 0
    follow_latest_chat: bool = True

    def sessions(self) -> list[SessionSummary]:
        return self.services.store.list_sessions(limit=30)

    def reload_active_session(self) -> None:
        if not self.active_session_id:
            self.messages = []
            return
        self.messages = [
            (message.role, message.content)
            for message in self.services.store.get_messages(self.active_session_id)
        ]
        self.chat_scroll = 0
        self.follow_latest_chat = True

    def reset_session(self) -> None:
        self.active_session_id = None
        self.messages = []
        self.execution_log = []
        self.status = "Started a new session."
        self.chat_scroll = 0
        self.follow_latest_chat = True

    def add_local_message(self, content: str) -> None:
        self.messages.append(("assistant", content))


def _relative_time(raw: str) -> str:
    try:
        then = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    now = datetime.now(UTC)
    delta = now - then.astimezone(UTC)
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    days = seconds // 86400
    return f"{days}d ago"


class AirgentTUI:
    def __init__(self, *, services: AppServices, agent_key: str, max_turns: int | None) -> None:
        self.state = TUIState(services=services, agent_key=agent_key, max_turns=max_turns)
        self.input_buffer = Buffer(multiline=True)
        self.input_buffer.on_text_changed += self._on_input_change
        self.banner = FormattedTextControl(self._render_banner)
        self.rule = FormattedTextControl(self._render_rule)
        self.chat_panel = FormattedTextControl(self._render_chat)
        self.chat_window = Window(
            content=self.chat_panel,
            wrap_lines=True,
            always_hide_cursor=True,
            get_vertical_scroll=self._get_chat_vertical_scroll,
            allow_scroll_beyond_bottom=True,
            style="class:screen",
        )
        self.palette_panel = FormattedTextControl(self._render_palette)
        self.detail_panel = FormattedTextControl(self._render_detail_panel)
        self.prompt_prefix = FormattedTextControl(self._render_prompt_prefix)
        self.footer = FormattedTextControl(self._render_footer)
        self.input = BufferControl(
            buffer=self.input_buffer,
            input_processors=[
                ConditionalProcessor(
                    processor=PlaceholderProcessor(
                        lambda: "Ask Airgent...  / for commands  /resume to restore a session"
                    ),
                    filter=Condition(lambda: True),
                )
            ],
        )
        self.application = Application(
            full_screen=True,
            layout=self._build_layout(),
            key_bindings=self._build_bindings(),
            style=self._build_style(),
            mouse_support=True,
            after_render=self._after_render,
        )

    def _build_style(self) -> Style:
        return Style.from_dict(
            {
                "": "",
                "screen": "",
                "banner.title": "bold",
                "banner.meta": "ansibrightblack",
                "banner.path": "ansicyan",
                "banner.pet": "#d99672",
                "rule": "ansibrightblack",
                "prompt": "bold",
                "placeholder": "ansibrightblack italic",
                "user.label": "bold #d9b18f",
                "assistant.label": "bold #a9c7c2",
                "body": "",
                "muted": "ansibrightblack",
                "status": "ansibrightblack",
                "palette.title": "bold #a9c7c2",
                "palette.search": "ansibrightblack",
                "palette.current": "reverse",
                "palette.normal": "",
                "palette.meta": "ansibrightblack",
                "event.tool": "#d9b18f",
                "event.thinking": "#8db3aa",
                "event.message": "#a9c7c2",
                "event.output": "ansibrightblack",
                "detail.title": "bold #a9c7c2",
                "detail.body": "",
                "footer": "ansibrightblack",
            }
        )

    def _build_layout(self) -> Layout:
        prompt_row = VSplit(
            [
                Window(
                    content=self.prompt_prefix,
                    width=2,
                    height=1,
                    dont_extend_width=True,
                    style="class:screen",
                ),
                Window(
                    content=self.input,
                    wrap_lines=True,
                    height=Dimension(min=1, max=6, preferred=2),
                    style="class:screen",
                ),
            ],
            style="class:screen",
        )
        root = HSplit(
            [
                Window(content=self.banner, height=7, style="class:screen"),
                Window(content=self.rule, height=1, style="class:screen"),
                self.chat_window,
                ConditionalContainer(
                    Window(
                        content=self.detail_panel,
                        wrap_lines=True,
                        always_hide_cursor=True,
                        height=Dimension(min=6, max=16, preferred=10),
                        style="class:screen",
                    ),
                    filter=Condition(lambda: self.state.detail_open),
                ),
                ConditionalContainer(
                    Window(
                        content=self.palette_panel,
                        wrap_lines=True,
                        always_hide_cursor=True,
                        height=Dimension(min=4, max=14, preferred=10),
                        style="class:screen",
                    ),
                    filter=Condition(self._is_slash_mode),
                ),
                Window(content=self.rule, height=1, style="class:screen"),
                prompt_row,
                Window(content=self.rule, height=1, style="class:screen"),
                Window(content=self.footer, height=1, style="class:screen"),
            ],
            style="class:screen",
        )
        return Layout(root, focused_element=self.input)

    def _build_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-q")
        @kb.add("c-c")
        def _quit(event) -> None:
            event.app.exit()

        @kb.add("c-o")
        def _toggle_detail(event) -> None:
            self.state.detail_open = not self.state.detail_open
            self.state.status = "Execution details open." if self.state.detail_open else "Execution details hidden."
            self._invalidate()

        @kb.add("pageup", eager=True)
        def _chat_page_up(event) -> None:
            self._scroll_chat(-self._chat_page_size())

        @kb.add("pagedown", eager=True)
        def _chat_page_down(event) -> None:
            self._scroll_chat(self._chat_page_size())

        @kb.add("home", eager=True)
        def _chat_home(event) -> None:
            self._scroll_chat_to(0, follow_latest=False)

        @kb.add("end", eager=True)
        def _chat_end(event) -> None:
            self._scroll_chat_to(self._chat_max_scroll(), follow_latest=True)

        @kb.add("enter", eager=True)
        def _enter(event) -> None:
            if self._is_slash_mode():
                event.app.create_background_task(self._execute_palette_selection())
                return
            event.app.create_background_task(self._submit())

        @kb.add("escape", "enter", eager=True)
        def _newline(event) -> None:
            self.input_buffer.insert_text("\n")

        @kb.add("up", filter=Condition(self._is_slash_mode))
        def _prev_item(event) -> None:
            items = self._palette_items()
            if not items:
                return
            self.state.palette_index = max(0, self.state.palette_index - 1)
            self._invalidate()

        @kb.add("down", filter=Condition(self._is_slash_mode))
        def _next_item(event) -> None:
            items = self._palette_items()
            if not items:
                return
            self.state.palette_index = min(len(items) - 1, self.state.palette_index + 1)
            self._invalidate()

        @kb.add("tab", filter=Condition(self._is_slash_mode))
        def _cycle_item(event) -> None:
            items = self._palette_items()
            if not items:
                return
            self.state.palette_index = (self.state.palette_index + 1) % len(items)
            self._invalidate()

        @kb.add("escape", filter=Condition(self._is_slash_mode))
        def _cancel_palette(event) -> None:
            self.input_buffer.text = ""
            self.state.status = "Exited command palette."
            self._invalidate()

        return kb

    def _base_commands(self) -> list[SlashCommand]:
        return [
            SlashCommand("/new", "Start a fresh conversation"),
            SlashCommand("/resume", "Search and resume a previous session"),
            SlashCommand("/memory", "Preview recent long-term memory"),
            SlashCommand("/reload", "Reload the current transcript from local storage"),
            SlashCommand("/help", "Show slash command help"),
            SlashCommand("/quit", "Exit the TUI"),
        ]

    def _input_text(self) -> str:
        return self.input_buffer.text.strip()

    def _is_slash_mode(self) -> bool:
        return self._input_text().startswith("/") and "\n" not in self.input_buffer.text

    def _is_resume_mode(self) -> bool:
        return self._input_text().startswith("/resume")

    def _resume_query(self) -> str:
        text = self._input_text()
        if not text.startswith("/resume"):
            return ""
        return text[len("/resume") :].strip().lower()

    def _matching_commands(self) -> list[SlashCommand]:
        text = self._input_text()
        if not text.startswith("/"):
            return []
        needle = text[1:].strip().lower()
        commands = self._base_commands()
        if not needle:
            return commands
        return [
            command
            for command in commands
            if needle in command.name[1:].lower() or needle in command.description.lower()
        ]

    def _matching_sessions(self) -> list[SessionSummary]:
        query = self._resume_query()
        sessions = self.state.sessions()
        if not query:
            return sessions
        matched = []
        for session in sessions:
            haystack = " ".join(
                [
                    session.session_id.lower(),
                    session.title.lower(),
                    (session.last_message or "").lower(),
                ]
            )
            if query in haystack:
                matched.append(session)
        return matched

    def _palette_items(self) -> list[object]:
        return self._matching_sessions() if self._is_resume_mode() else self._matching_commands()

    def _on_input_change(self, _) -> None:
        self.state.palette_index = 0
        self._invalidate()

    def _render_banner(self) -> StyleAndTextTuples:
        project_root = str(self.state.services.settings.project_root)
        session_label = self.state.active_session_id or "new"
        pet_lines = [
            "  .==.  ",
            " [oooo] ",
            " [|__|] ",
            " /|  |\\ ",
            "  /  \\  ",
        ]
        fragments: StyleAndTextTuples = []
        info_lines = [
            ("class:banner.title", "airgent"),
            ("class:banner.meta", f"{self.state.agent_key}  ·  local-first agent runtime"),
            ("class:banner.path", project_root),
            ("class:banner.meta", "Type /resume to continue a previous conversation"),
            ("class:banner.meta", f"Session: {session_label}"),
        ]
        for index in range(5):
            fragments.append(("class:banner.pet", pet_lines[index]))
            fragments.append(("", "   "))
            fragments.append(info_lines[index])
            fragments.append(("", "\n"))
        return fragments

    def _render_rule(self) -> StyleAndTextTuples:
        return [("class:rule", "─" * 400)]

    def _render_prompt_prefix(self) -> StyleAndTextTuples:
        return [("class:prompt", "› ")]

    def _render_chat(self) -> StyleAndTextTuples:
        if not self.state.messages:
            fragments: StyleAndTextTuples = [
                ("class:muted", "No messages yet. Start here, or type /resume to restore a saved session.")
            ]
        else:
            fragments = []
            for role, content in self.state.messages[-18:]:
                label_style = "class:user.label" if role == "user" else "class:assistant.label"
                label = "You" if role == "user" else "Airgent"
                fragments.append((label_style, f"{label}\n"))
                fragments.append(("class:body", f"{content}\n\n"))

        if self.state.busy:
            fragments.extend(self._render_active_thread())
        return fragments

    def _render_detail_panel(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = [("class:detail.title", "Run Trace  (Ctrl-O to close)\n\n")]
        if not self.state.execution_log:
            fragments.append(("class:muted", "No execution details yet."))
            return fragments
        for entry in self.state.execution_log:
            fragments.append(("class:detail.title", f"{entry.summary}\n"))
            if entry.detail:
                fragments.append(("class:detail.body", f"{entry.detail}\n"))
            fragments.append(("", "\n"))
        return fragments

    def _render_palette(self) -> StyleAndTextTuples:
        if self._is_resume_mode():
            return self._render_resume_palette()
        return self._render_command_palette()

    def _render_command_palette(self) -> StyleAndTextTuples:
        commands = self._matching_commands()
        if not commands:
            return [("class:palette.normal", "No matching command.")]

        fragments: StyleAndTextTuples = [("class:palette.title", "Command Palette\n")]
        fragments.append(("class:palette.search", "Type a slash command and press Enter\n\n"))
        for index, command in enumerate(commands):
            style = "class:palette.current" if index == self.state.palette_index else "class:palette.normal"
            fragments.append((style, f"{command.name}\n"))
            fragments.append(("class:palette.meta", f"  {command.description}\n\n"))
        return fragments

    def _render_resume_palette(self) -> StyleAndTextTuples:
        sessions = self._matching_sessions()
        query = self._resume_query() or "all sessions"
        fragments: StyleAndTextTuples = [("class:palette.title", "Resume Session\n")]
        fragments.append(("class:palette.search", f"Search: {query}\n\n"))
        if not sessions:
            fragments.append(("class:palette.normal", "No matching sessions."))
            return fragments

        for index, session in enumerate(sessions):
            style = "class:palette.current" if index == self.state.palette_index else "class:palette.normal"
            preview = (session.last_message or "").replace("\n", " ")
            preview = preview[:92] + ("..." if len(preview) > 92 else "")
            fragments.append((style, f"{session.title}\n"))
            fragments.append(
                ("class:palette.meta", f"  {_relative_time(session.updated_at)}  ·  {session.session_id}\n")
            )
            if preview:
                fragments.append(("class:palette.meta", f"  {preview}\n"))
            fragments.append(("", "\n"))
        return fragments

    def _render_active_thread(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = [
            ("class:assistant.label", "Airgent\n"),
            ("class:muted", "Working\n"),
        ]
        if not self.state.execution_log:
            fragments.append(("class:muted", "· Starting the run\n\n"))
            return fragments

        for entry in self.state.execution_log[-6:]:
            style = {
                "tool": "class:event.tool",
                "thinking": "class:event.thinking",
                "message": "class:event.message",
                "agent": "class:event.message",
            }.get(entry.kind, "class:event.output")
            summary = entry.summary if len(entry.summary) <= 96 else f"{entry.summary[:93]}..."
            fragments.append((style, f"· {summary}\n"))
        fragments.append(("", "\n"))
        return fragments

    def _render_footer(self) -> StyleAndTextTuples:
        return [
            ("class:footer", f"{self.state.services.settings.project_root.name}"),
            ("class:footer", "  |  "),
            ("class:footer", self.state.agent_key),
            ("class:footer", "  |  "),
            ("class:footer", "PgUp/PgDn scroll"),
            ("class:footer", "  |  "),
            ("class:footer", "Ctrl-O details"),
            ("class:footer", "  |  "),
            ("class:status", self.state.status),
        ]

    def _chat_max_scroll(self) -> int:
        info = self.chat_window.render_info
        if info is None:
            return max(self.state.chat_scroll, 0)
        return max(info.content_height - info.window_height, 0)

    def _chat_page_size(self) -> int:
        info = self.chat_window.render_info
        if info is None:
            return 8
        return max(info.window_height - 2, 1)

    def _get_chat_vertical_scroll(self, window: Window) -> int:
        info = window.render_info
        if info is None:
            return max(self.state.chat_scroll, 0)
        max_scroll = max(info.content_height - info.window_height, 0)
        if self.state.follow_latest_chat:
            self.state.chat_scroll = max_scroll
        else:
            self.state.chat_scroll = min(max(self.state.chat_scroll, 0), max_scroll)
        return self.state.chat_scroll

    def _scroll_chat(self, delta: int) -> None:
        self._scroll_chat_to(self.state.chat_scroll + delta)

    def _scroll_chat_to(self, target: int, *, follow_latest: bool | None = None) -> None:
        max_scroll = self._chat_max_scroll()
        self.state.chat_scroll = min(max(target, 0), max_scroll)
        if follow_latest is None:
            self.state.follow_latest_chat = self.state.chat_scroll >= max_scroll
        else:
            self.state.follow_latest_chat = follow_latest and self.state.chat_scroll >= max_scroll
        self.state.status = "Following latest output." if self.state.follow_latest_chat else "Browsing earlier output."
        self._invalidate()

    def _after_render(self, app) -> None:
        info = self.chat_window.render_info
        if info is None:
            return
        max_scroll = max(info.content_height - info.window_height, 0)
        desired_scroll = max_scroll if self.state.follow_latest_chat else min(self.state.chat_scroll, max_scroll)
        if desired_scroll != self.state.chat_scroll:
            self.state.chat_scroll = desired_scroll
            app.invalidate()

    async def _execute_palette_selection(self) -> None:
        items = self._palette_items()
        if not items:
            self.state.status = "Nothing to select."
            self._invalidate()
            return

        selected = items[self.state.palette_index]
        self.input_buffer.text = ""

        if isinstance(selected, SessionSummary):
            self.state.active_session_id = selected.session_id
            self.state.reload_active_session()
            self.state.status = f"Resumed session {selected.session_id}"
            self._invalidate()
            return

        command = selected
        if command.name == "/new":
            self.state.reset_session()
        elif command.name == "/resume":
            self.input_buffer.text = "/resume "
            self.state.status = "Search sessions and press Enter to resume."
        elif command.name == "/memory":
            memories = self.state.services.store.list_memories(limit=5)
            if not memories:
                self.state.add_local_message("No long-term memory saved yet.")
            else:
                lines = ["Recent memory:"]
                for memory in memories:
                    tags = f" [tags: {', '.join(memory.tags)}]" if memory.tags else ""
                    lines.append(f"- {memory.content}{tags}")
                self.state.add_local_message("\n".join(lines))
            self.state.status = "Loaded recent memory."
        elif command.name == "/reload":
            self.state.reload_active_session()
            self.state.status = "Reloaded current transcript."
        elif command.name == "/help":
            self.state.add_local_message(
                "\n".join(
                    [
                        "Slash commands:",
                        "- /new",
                        "- /resume",
                        "- /memory",
                        "- /reload",
                        "- /help",
                        "- /quit",
                    ]
                )
            )
            self.state.status = "Loaded slash command help."
        elif command.name == "/quit":
            self.application.exit()
            return

        self._invalidate()

    async def _submit(self) -> None:
        if self.state.busy:
            return
        prompt = self.input_buffer.text.strip()
        if not prompt:
            self.state.status = "Composer is empty."
            self._invalidate()
            return

        self.state.busy = True
        self.state.status = "Waiting for model..."
        self.state.execution_log = []
        self.state.follow_latest_chat = True
        self.input_buffer.text = ""
        if self.state.active_session_id is None:
            self.state.messages = []
        self.state.messages.append(("user", prompt))
        self._invalidate()

        try:
            async for event in self.state.services.runner.stream(
                AgentRunRequest(
                    input=prompt,
                    session_id=self.state.active_session_id,
                    agent_key=self.state.agent_key,
                    max_turns=self.state.max_turns,
                ),
                request_id="tui",
            ):
                self._apply_progress_event(event)
                self._invalidate()
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.state.add_local_message(f"Error: {message}")
            self.state.status = message
        finally:
            self.state.busy = False
            self._invalidate()

    def _invalidate(self) -> None:
        get_app().invalidate()

    def _apply_progress_event(self, event: AgentProgressEvent) -> None:
        if event.kind == "status":
            self.state.active_session_id = event.session_id or self.state.active_session_id
            self.state.status = event.summary
            return
        if event.kind == "completed":
            self.state.active_session_id = event.session_id or self.state.active_session_id
            self.state.reload_active_session()
            self.state.status = "Run completed."
            return
        entry = ExecutionEntry(kind=event.kind, summary=event.summary, detail=event.detail)
        if self.state.execution_log and self.state.execution_log[-1].summary == entry.summary:
            if entry.detail:
                self.state.execution_log[-1] = entry
        else:
            self.state.execution_log.append(entry)
        if event.kind == "thinking":
            self.state.status = "Thinking..."
        elif event.kind == "tool":
            self.state.status = event.summary
        else:
            self.state.status = event.summary

    async def run(self) -> None:
        self.state.reload_active_session()
        await self.application.run_async()


async def run_tui(*, services: AppServices, agent_key: str, max_turns: int | None) -> None:
    await AirgentTUI(services=services, agent_key=agent_key, max_turns=max_turns).run()
