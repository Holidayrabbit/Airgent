from __future__ import annotations

from dataclasses import dataclass, field
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
from prompt_toolkit.widgets import Frame

from app.api.schemas.agent import AgentRunRequest
from app.bootstrap import AppServices


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


@dataclass
class TUIState:
    services: AppServices
    agent_key: str
    max_turns: int | None
    active_session_id: str | None = None
    selected_session_index: int = 0
    messages: list[tuple[str, str]] = field(default_factory=list)
    status: str = "Ready"
    busy: bool = False
    command_index: int = 0

    def sessions(self):
        return self.services.store.list_sessions(limit=10)

    def reload_active_session(self) -> None:
        if not self.active_session_id:
            self.messages = []
            return
        self.messages = [
            (message.role, message.content)
            for message in self.services.store.get_messages(self.active_session_id)
        ]

    def select_index(self, delta: int) -> None:
        sessions = self.sessions()
        if not sessions:
            return
        self.selected_session_index = max(0, min(len(sessions) - 1, self.selected_session_index + delta))

    def select_active_or_first(self) -> None:
        sessions = self.sessions()
        if not sessions:
            self.selected_session_index = 0
            return
        if self.active_session_id is None:
            self.selected_session_index = 0
            return
        for index, session in enumerate(sessions):
            if session.session_id == self.active_session_id:
                self.selected_session_index = index
                return
        self.selected_session_index = 0

    def load_selected_session(self) -> None:
        sessions = self.sessions()
        if not sessions:
            self.active_session_id = None
            self.messages = []
            self.status = "No saved sessions."
            return
        session = sessions[self.selected_session_index]
        self.active_session_id = session.session_id
        self.reload_active_session()
        self.status = f"Loaded session {session.session_id}"

    def reset_session(self) -> None:
        self.active_session_id = None
        self.messages = []
        self.selected_session_index = 0
        self.status = "Started a new session."

    def add_local_message(self, content: str) -> None:
        self.messages.append(("assistant", content))


class AirgentTUI:
    def __init__(self, *, services: AppServices, agent_key: str, max_turns: int | None) -> None:
        self.state = TUIState(services=services, agent_key=agent_key, max_turns=max_turns)
        self.input_buffer = Buffer(multiline=True)
        self.input_buffer.on_text_changed += self._on_input_change
        self.header = FormattedTextControl(self._render_header)
        self.sessions_panel = FormattedTextControl(self._render_sessions)
        self.chat_panel = FormattedTextControl(self._render_chat)
        self.command_panel = FormattedTextControl(self._render_commands)
        self.footer = FormattedTextControl(self._render_footer)
        self.input = BufferControl(
            buffer=self.input_buffer,
            input_processors=[
                ConditionalProcessor(
                    processor=PlaceholderProcessor(
                        lambda: "Ask Airgent...  Enter send  / commands  Esc+Enter newline"
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
        )

    def _build_style(self) -> Style:
        return Style.from_dict(
            {
                "frame.border": "#7a6852",
                "frame.label": "bold #c86337",
                "header": "bg:#f4ecdd #1f1a15 bold",
                "sidebar": "#5f5549",
                "session.current": "bg:#e8d7bd #201913 bold",
                "session.normal": "#6b6156",
                "command.current": "bg:#e8d7bd #201913 bold",
                "command.normal": "#5f5549",
                "role.user": "bold #8f3a1d",
                "role.assistant": "bold #285d52",
                "body": "#201913",
                "footer": "bg:#ece1ce #4a4036",
                "placeholder": "#938474 italic",
            }
        )

    def _build_layout(self) -> Layout:
        body = VSplit(
            [
                Frame(
                    Window(
                        content=self.sessions_panel,
                        wrap_lines=False,
                        always_hide_cursor=True,
                    ),
                    title="Recent Sessions",
                    width=Dimension(preferred=30, min=24, max=34),
                ),
                Frame(
                    Window(
                        content=self.chat_panel,
                        wrap_lines=True,
                        always_hide_cursor=True,
                    ),
                    title="Transcript",
                ),
            ]
        )
        root = HSplit(
            [
                Window(content=self.header, height=2, style="class:header"),
                body,
                ConditionalContainer(
                    Frame(
                        Window(
                            content=self.command_panel,
                            wrap_lines=True,
                            always_hide_cursor=True,
                            height=Dimension(min=3, max=8, preferred=5),
                        ),
                        title="Commands",
                    ),
                    filter=Condition(self._is_command_mode),
                ),
                Frame(
                    Window(
                        content=self.input,
                        wrap_lines=True,
                        height=Dimension(min=4, max=8, preferred=5),
                    ),
                    title="Composer",
                ),
                Window(content=self.footer, height=1, style="class:footer"),
            ]
        )
        return Layout(root, focused_element=self.input)

    def _build_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-q")
        @kb.add("c-c")
        def _quit(event) -> None:
            event.app.exit()

        @kb.add("enter", eager=True)
        def _enter(event) -> None:
            if self._is_command_mode():
                event.app.create_background_task(self._execute_selected_command())
                return
            event.app.create_background_task(self._submit())

        @kb.add("escape", "enter", eager=True)
        def _newline(event) -> None:
            self.input_buffer.insert_text("\n")

        @kb.add("up", filter=Condition(self._is_command_mode))
        def _command_prev(event) -> None:
            commands = self._matching_commands()
            if not commands:
                return
            self.state.command_index = max(0, self.state.command_index - 1)
            self._invalidate()

        @kb.add("down", filter=Condition(self._is_command_mode))
        def _command_next(event) -> None:
            commands = self._matching_commands()
            if not commands:
                return
            self.state.command_index = min(len(commands) - 1, self.state.command_index + 1)
            self._invalidate()

        @kb.add("tab", filter=Condition(self._is_command_mode))
        def _command_cycle(event) -> None:
            commands = self._matching_commands()
            if not commands:
                return
            self.state.command_index = (self.state.command_index + 1) % len(commands)
            self._invalidate()

        @kb.add("escape", filter=Condition(self._is_command_mode))
        def _clear_command(event) -> None:
            self.input_buffer.text = ""
            self.state.status = "Exited command panel."
            self._invalidate()

        return kb

    def _base_commands(self) -> list[SlashCommand]:
        commands = [
            SlashCommand("/new", "Start a new chat session"),
            SlashCommand("/reload", "Refresh recent sessions and current transcript"),
            SlashCommand("/memory", "Preview recent long-term memory"),
            SlashCommand("/help", "Show slash command help"),
            SlashCommand("/quit", "Exit the TUI"),
        ]
        for index, session in enumerate(self.state.sessions()[:5], start=1):
            commands.append(SlashCommand(f"/resume {index}", f"Resume: {session.title} ({session.session_id})"))
        return commands

    def _matching_commands(self) -> list[SlashCommand]:
        query = self.input_buffer.text.strip()
        if not query.startswith("/"):
            return []
        needle = query[1:].strip().lower()
        commands = self._base_commands()
        if not needle:
            return commands
        return [
            command
            for command in commands
            if needle in command.name[1:].lower() or needle in command.description.lower()
        ]

    def _is_command_mode(self) -> bool:
        query = self.input_buffer.text.strip()
        return query.startswith("/") and "\n" not in self.input_buffer.text

    def _on_input_change(self, _) -> None:
        self.state.command_index = 0
        self._invalidate()

    def _render_header(self) -> StyleAndTextTuples:
        session_label = self.state.active_session_id or "new"
        status = "Working" if self.state.busy else "Idle"
        return [
            ("class:header", " Airgent "),
            ("class:header", f"  session:{session_label}  "),
            ("class:header", f"agent:{self.state.agent_key}  "),
            ("class:header", f"status:{status}"),
        ]

    def _render_sessions(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        sessions = self.state.sessions()
        if not sessions:
            return [("class:sidebar", "No local sessions yet.")]

        for index, session in enumerate(sessions):
            style = "class:session.current" if index == self.state.selected_session_index else "class:session.normal"
            marker = "> " if index == self.state.selected_session_index else "  "
            preview = (session.last_message or "").replace("\n", " ")
            preview = preview[:26] + ("..." if len(preview) > 26 else "")
            fragments.extend(
                [
                    (style, f"{marker}{session.title}\n"),
                    (style, f"   {session.session_id}  {preview}\n\n"),
                ]
            )
        return fragments

    def _render_commands(self) -> StyleAndTextTuples:
        commands = self._matching_commands()
        if not commands:
            return [("class:command.normal", "No matching command.")]

        fragments: StyleAndTextTuples = []
        for index, command in enumerate(commands):
            style = "class:command.current" if index == self.state.command_index else "class:command.normal"
            fragments.extend(
                [
                    (style, f"{command.name}\n"),
                    (style, f"  {command.description}\n\n"),
                ]
            )
        return fragments

    def _render_chat(self) -> StyleAndTextTuples:
        if not self.state.messages:
            return [("class:body", "No messages yet. Start a new conversation below.")]

        fragments: StyleAndTextTuples = []
        for role, content in self.state.messages[-16:]:
            role_style = "class:role.user" if role == "user" else "class:role.assistant"
            label = "You" if role == "user" else "Airgent"
            fragments.append((role_style, f"{label}\n"))
            fragments.append(("class:body", f"{content}\n\n"))
        return fragments

    def _render_footer(self) -> StyleAndTextTuples:
        return [
            ("class:footer", " Enter send "),
            ("class:footer", " / commands "),
            ("class:footer", " Up/Down choose "),
            ("class:footer", " Esc+Enter newline "),
            ("class:footer", f" {self.state.status}"),
        ]

    async def _execute_selected_command(self) -> None:
        commands = self._matching_commands()
        if not commands:
            self.state.status = "No command selected."
            self._invalidate()
            return

        command = commands[self.state.command_index]
        self.input_buffer.text = ""

        if command.name == "/new":
            self.state.reset_session()
        elif command.name == "/reload":
            self.state.select_active_or_first()
            self.state.reload_active_session()
            self.state.status = "Refreshed local state."
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
        elif command.name == "/help":
            self.state.add_local_message(
                "\n".join(
                    [
                        "Slash commands:",
                        "- /new",
                        "- /reload",
                        "- /memory",
                        "- /resume N",
                        "- /quit",
                    ]
                )
            )
            self.state.status = "Loaded command help."
        elif command.name == "/quit":
            self.application.exit()
            return
        elif command.name.startswith("/resume "):
            index = int(command.name.split(" ", maxsplit=1)[1]) - 1
            sessions = self.state.sessions()
            if 0 <= index < len(sessions):
                self.state.active_session_id = sessions[index].session_id
                self.state.select_active_or_first()
                self.state.reload_active_session()
                self.state.status = f"Resumed session {sessions[index].session_id}"
            else:
                self.state.status = "Session selection is out of range."

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
        self.input_buffer.text = ""
        if self.state.active_session_id is None:
            self.state.messages = []
        self.state.messages.append(("user", prompt))
        self._invalidate()

        try:
            result = await self.state.services.runner.run(
                AgentRunRequest(
                    input=prompt,
                    session_id=self.state.active_session_id,
                    agent_key=self.state.agent_key,
                    max_turns=self.state.max_turns,
                ),
                request_id="tui",
            )
            self.state.active_session_id = result.session_id
            self.state.reload_active_session()
            self.state.select_active_or_first()
            self.state.status = f"Done. memory_hits={result.context['memory_hits']}"
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.state.add_local_message(f"Error: {message}")
            self.state.status = message
        finally:
            self.state.busy = False
            self._invalidate()

    def _invalidate(self) -> None:
        get_app().invalidate()

    async def run(self) -> None:
        self.state.select_active_or_first()
        self.state.reload_active_session()
        await self.application.run_async()


async def run_tui(*, services: AppServices, agent_key: str, max_turns: int | None) -> None:
    await AirgentTUI(services=services, agent_key=agent_key, max_turns=max_turns).run()
