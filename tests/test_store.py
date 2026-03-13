from __future__ import annotations

from pathlib import Path

from app.memory.store import LocalStore


def test_store_persists_session_and_memory(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "airgent.db")
    store.append_message("sess-1", role="user", content="hello world", agent_key="root_assistant")
    store.append_message("sess-1", role="assistant", content="hi there", agent_key="root_assistant")
    store.append_session_items("sess-1", [{"role": "user", "content": "hello world"}])

    memory = store.add_memory("User prefers concise answers", tags=["style"], source_session_id="sess-1")

    sessions = store.list_sessions()
    messages = store.get_messages("sess-1")
    session_items = store.get_session_items("sess-1")
    memories = store.search_memories("concise answers")

    assert sessions[0].session_id == "sess-1"
    assert len(messages) == 2
    assert session_items[0]["role"] == "user"
    assert memories[0].id == memory.id
