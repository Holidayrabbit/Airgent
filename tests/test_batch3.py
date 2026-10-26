from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.kernel.manager import PiKernel, _load_stream_rules
from app.memory.embeddings import EmbeddingClient
from app.memory.store import LocalStore, _reciprocal_rank_fusion
from app.mcp_server import McpServer


# ---------------------------------------------------------------------------
# Hybrid memory retrieval
# ---------------------------------------------------------------------------
class FakeEmbedder:
    """Deterministic word-bag embeddings: unknown texts average known words."""

    model = "fake-embed"

    def __init__(self, word_vectors: dict[str, list[float]], dimension: int = 4) -> None:
        self.word_vectors = word_vectors
        self.dimension = dimension

    def embed_one(self, text: str) -> list[float]:
        tokens = [token.strip(".,!") for token in text.lower().split()]
        vectors = [self.word_vectors[token] for token in tokens if token in self.word_vectors]
        out = [0.0] * self.dimension
        for vector in vectors:
            for i in range(min(len(vector), self.dimension)):
                out[i] += vector[i]
        if vectors:
            out = [value / len(vectors) for value in out]
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]


def _vec_store(tmp_path: Path) -> LocalStore:
    pytest.importorskip("sqlite_vec")
    word_vectors = {
        "python": [0.9, 0.1, 0.0, 0.0],
        "packaging": [0.8, 0.2, 0.0, 0.0],
        "guide": [0.9, 0.0, 0.1, 0.0],
        "docker": [0.0, 0.0, 0.9, 0.1],
        "deployment": [0.0, 0.1, 0.8, 0.2],
        "notes": [0.0, 0.0, 0.85, 0.1],
        # Semantic-only query words (never appear in memory contents).
        "pip": [0.85, 0.2, 0.0, 0.0],
        "wheels": [0.8, 0.3, 0.0, 0.0],
    }
    store = LocalStore(
        tmp_path / "vec.db",
        embedder=FakeEmbedder(word_vectors),
    )
    store.add_memory("python packaging guide")
    store.add_memory("docker deployment notes")
    return store


def test_rrf_fusion_prefers_records_in_both_rankings() -> None:
    a = LocalStore._memory_from_row  # noqa: F841 - keep import shape clear
    from app.memory.store import MemoryRecord

    def record(memory_id: str) -> MemoryRecord:
        return MemoryRecord(id=memory_id, content=memory_id, tags=[], source_session_id=None, created_at="")

    keyword = [record("kw-only"), record("both"), record("kw2")]
    vector = [record("vec-only"), record("both"), record("vec2")]

    fused = _reciprocal_rank_fusion(keyword, vector, limit=5)

    assert fused[0].id == "both"  # appears in both lists
    ids = [item.id for item in fused]
    assert {"kw-only", "vec-only"} <= set(ids)


def test_vector_backfill_and_hybrid_search(tmp_path: Path) -> None:
    store = _vec_store(tmp_path)

    # add_memory already embeds; wipe to exercise the backfill path.
    with store._connect() as connection:
        connection.execute("DELETE FROM memory_vec")
        connection.execute("DELETE FROM memory_embeddings")
    assert store.embed_missing_memories() == 2
    assert store.embed_missing_memories() == 0  # idempotent

    ranked = store.search_memories("packaging", limit=2)
    assert ranked[0].content == "python packaging guide"

    # Pure-vector hit: "pip wheels" shares no keyword with any memory.
    semantic = store.search_memories("pip wheels", limit=2)
    assert semantic[0].content == "python packaging guide"


def test_vector_dimension_change_recreates_table(tmp_path: Path) -> None:
    pytest.importorskip("sqlite_vec")
    store = _vec_store(tmp_path)
    store.embed_missing_memories()

    store.embedder.dimension = 8  # model switched
    store.embed_missing_memories()

    with store._connect() as connection:
        meta = connection.execute("SELECT dimension FROM vector_meta WHERE id = 1").fetchone()
        count = connection.execute("SELECT COUNT(*) AS n FROM memory_embeddings").fetchone()
    assert meta["dimension"] == 8
    assert count["n"] == 2


def test_keyword_only_when_vec_extension_missing(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "plain.db", embedder=FakeEmbedder({"x": [1.0, 0.0, 0.0, 0.0]}))
    store.add_memory("python packaging guide")

    ranked = store.search_memories("packaging", limit=3)
    assert [record.content for record in ranked] == ["python packaging guide"]


def test_embedding_client_parses_and_reports_errors() -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, str]:
        captured["url"] = url
        captured["body"] = json.loads(body)
        payload = {"data": [{"embedding": [0.1, 0.2]}]}
        return 200, json.dumps(payload)

    client = EmbeddingClient(
        base_url="https://proxy.example.com/v1",
        api_key="sk-test",
        model="fake-embed",
        post=fake_post,
    )
    vector = client.embed_one("hello")
    assert vector == [0.1, 0.2]
    assert client.dimension == 2
    assert captured["url"] == "https://proxy.example.com/v1/embeddings"
    assert captured["body"]["model"] == "fake-embed"

    def failing_post(url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, str]:
        return 503, "boom"

    broken = EmbeddingClient(
        base_url="https://x/v1", api_key="k", model="m", post=failing_post
    )
    with pytest.raises(Exception):
        broken.embed_one("hello")


# ---------------------------------------------------------------------------
# Skills install
# ---------------------------------------------------------------------------
def _write_local_skill_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True)
    (repo_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Installed via git.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
                "HOME": str(repo_dir),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "skill")


def test_skills_install_and_remove(tmp_path: Path) -> None:
    from app.skills.registry import SkillRegistry

    repo = tmp_path / "source-repo"
    _write_local_skill_repo(repo)

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        skills_root=tmp_path / "project-skills",
        skills_install_root=tmp_path / "data" / "skills",
    )
    registry = SkillRegistry(settings)

    entry = registry.install(str(repo), name="demo-skill")
    assert entry.name == "demo-skill"
    assert (settings.skills_install_dir / "demo-skill" / "SKILL.md").is_file()

    names = [item.name for item in registry.discover()]
    assert "demo-skill" in names

    assert registry.remove("demo-skill") is True
    assert registry.remove("demo-skill") is False
    assert "demo-skill" not in [item.name for item in registry.discover()]


def test_skills_install_rejects_bad_name_and_duplicate(tmp_path: Path) -> None:
    from app.skills.registry import SkillRegistry

    repo = tmp_path / "source-repo"
    _write_local_skill_repo(repo)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        skills_install_root=tmp_path / "data" / "skills",
    )
    registry = SkillRegistry(settings)

    with pytest.raises(ValueError):
        registry.install(str(repo), name="Bad_Name")

    registry.install(str(repo), name="demo-skill")
    with pytest.raises(ValueError):
        registry.install(str(repo), name="demo-skill")


def test_url_stem_helper() -> None:
    from app.skills.registry import _url_stem

    assert _url_stem("https://github.com/org/pi-skills.git") == "pi-skills"
    assert _url_stem("https://example.com/My-Repo/") == "my-repo"


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
def _mcp_services(tmp_path: Path) -> SimpleNamespace:
    from app.agents.runner import AgentExecutionResult

    store = LocalStore(tmp_path / "mcp.db")
    store.add_memory("Prefers uv", tags=["python"])

    async def fake_run(request, *, request_id: str) -> AgentExecutionResult:
        return AgentExecutionResult(
            request_id=request_id,
            session_id="sess-1",
            agent_key="root_assistant",
            output=f"echo: {request.input}",
            context={"model": "m", "pi_session_id": "pi-1"},
        )

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", project_root=tmp_path)
    return SimpleNamespace(store=store, settings=settings, runner=SimpleNamespace(run=fake_run))


def test_mcp_initialize_and_tools_list(tmp_path: Path) -> None:
    server = McpServer(_mcp_services(tmp_path))

    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    assert initialized["result"]["serverInfo"]["name"] == "airgent"

    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"memory_search", "memory_add", "sessions_list", "session_get", "agent_run"} <= names

    # Notifications produce no response.
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    error = server.handle({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
    assert error["error"]["code"] == -32601


def test_mcp_memory_and_session_tools(tmp_path: Path) -> None:
    services = _mcp_services(tmp_path)
    services.store.upsert_session("sess-1", "root_assistant")
    services.store.append_message("sess-1", role="user", content="hi", agent_key="root_assistant")
    server = McpServer(services)

    added = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "memory_add", "arguments": {"content": "Ships on arm64", "kind": "fact"}},
        }
    )
    assert added["result"]["isError"] is False

    searched = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": "uv"}},
        }
    )
    text = searched["result"]["content"][0]["text"]
    assert "Prefers uv" in text

    sessions = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sessions_list", "arguments": {}},
        }
    )
    assert "sess-1" in sessions["result"]["content"][0]["text"]

    transcript = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "session_get", "arguments": {"session_id": "sess-1"}},
        }
    )
    assert '"role": "user"' in transcript["result"]["content"][0]["text"]


def test_mcp_agent_run_and_tool_error(tmp_path: Path) -> None:
    server = McpServer(_mcp_services(tmp_path))

    run = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "agent_run", "arguments": {"input": "do the thing"}},
        }
    )
    payload = json.loads(run["result"]["content"][0]["text"])
    assert payload["output"] == "echo: do the thing"
    assert payload["session_id"] == "sess-1"

    unknown = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        }
    )
    assert unknown["error"]["code"] == -32602


def test_mcp_stdio_loop(tmp_path: Path, capsys) -> None:
    server = McpServer(_mcp_services(tmp_path))
    stdin = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        "not json",
        "",
    ]
    server.serve(iter(stdin))
    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == "airgent"
    assert json.loads(lines[1])["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Stream rules
# ---------------------------------------------------------------------------
def test_load_stream_rules(tmp_path: Path) -> None:
    config = tmp_path / "stream-rules.json"
    config.write_text(
        json.dumps(
            {
                "rules": [
                    {"pattern": "rm -rf /", "instruction": "Never destructive commands"},
                    {"pattern": "[invalid", "instruction": "skipped"},
                    {"pattern": "", "instruction": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rules = _load_stream_rules(config)
    assert len(rules) == 1
    assert rules[0].pattern == "rm -rf /"


class RulesFakeProcess:
    """Scripted process: per-prompt event lists, abort acknowledged."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self._scripts = scripts
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._prompt_index = -1
        self.aborts = 0
        self.prompts: list[str] = []
        self.alive = True

    async def request(self, command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if command_type == "prompt":
            self.prompts.append((payload or {}).get("message", ""))
            self._prompt_index += 1
            for record in self._scripts[self._prompt_index]:
                self._queue.put_nowait(record)
            return {}
        if command_type == "abort":
            self.aborts += 1
            return {}
        if command_type == "get_state":
            return {"isStreaming": False, "sessionId": "pi-1", "sessionFile": "s_pi-1.jsonl"}
        return {}

    async def respond_ui(self, request_id: str, payload: dict[str, Any]) -> None:
        pass

    async def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout or 0.05)
        except asyncio.TimeoutError:
            return None

    def task_done(self) -> None:
        pass

    async def close(self) -> None:
        self.alive = False


def _rules_kernel(tmp_path: Path, fake: RulesFakeProcess, *, max_retries: int = 2) -> PiKernel:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
        stream_rules_enabled=True,
        stream_rules_max_retries=max_retries,
        stream_rules_config_path=tmp_path / "rules.json",
    )
    (tmp_path / "rules.json").write_text(
        json.dumps({"rules": [{"pattern": "rm\\s+-rf\\s+/", "instruction": "No destructive commands."}]}),
        encoding="utf-8",
    )
    kernel = PiKernel(settings=settings, store=LocalStore(tmp_path / "store.db"))

    async def fake_spawn(mode: str = "default") -> RulesFakeProcess:
        return fake

    kernel._spawn = fake_spawn  # type: ignore[assignment]
    return kernel


_ABTRACTED_END = {"type": "agent_end", "messages": []}


def test_stream_rule_aborts_reinjects_and_retries(tmp_path: Path) -> None:
    fake = RulesFakeProcess(
        [
            [
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "I will rm -rf / now"}},
                _ABTRACTED_END,
            ],
            [
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Safe plan instead."}},
                {
                    "type": "agent_end",
                    "messages": [
                        {"role": "assistant", "content": [{"type": "text", "text": "Safe plan instead."}], "stopReason": "stop"}
                    ],
                },
            ],
        ]
    )
    kernel = _rules_kernel(tmp_path, fake)
    events: list[str] = []

    result = asyncio.run(
        kernel.run_prompt(
            session_id="s-rules",
            message="clean the disk",
            on_event=lambda event: events.append(event.kind),
            interactive=False,
        )
    )

    assert fake.aborts == 1
    assert result.text == "Safe plan instead."
    assert len(fake.prompts) == 2
    assert "stream rule" in fake.prompts[1]
    assert "No destructive commands." in fake.prompts[1]
    assert "clean the disk" in fake.prompts[1]
    assert "rule_triggered" in events


def test_stream_rule_gives_up_after_max_retries(tmp_path: Path) -> None:
    aborted = [
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "rm -rf / again"}},
        _ABTRACTED_END,
    ]
    fake = RulesFakeProcess([aborted, aborted, aborted])
    kernel = _rules_kernel(tmp_path, fake, max_retries=2)

    asyncio.run(kernel.run_prompt(session_id="s-loop", message="go", interactive=False))

    assert fake.aborts == 3  # 1 initial + 2 retries, then gives up
    assert len(fake.prompts) == 3


def test_no_rules_no_behavior_change(tmp_path: Path) -> None:
    fake = RulesFakeProcess(
        [
            [
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "plain answer"}},
                {
                    "type": "agent_end",
                    "messages": [
                        {"role": "assistant", "content": [{"type": "text", "text": "plain answer"}], "stopReason": "stop"}
                    ],
                },
            ]
        ]
    )
    kernel = _rules_kernel(tmp_path, fake)
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    kernel._stream_rules_cache = _load_stream_rules(tmp_path / "rules.json")

    result = asyncio.run(kernel.run_prompt(session_id="s-plain", message="hi", interactive=False))

    assert result.text == "plain answer"
    assert fake.aborts == 0
