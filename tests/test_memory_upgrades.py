from __future__ import annotations

import sqlite3
from pathlib import Path

from app.memory.auto_extract import extract_memories
from app.memory.store import LocalStore


def test_memory_kinds_and_importance_persist(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite")
    record = store.add_memory("Prefers uv", kind="preference", importance=0.9)

    loaded = store.get_memory(record.id)
    assert loaded.kind == "preference"
    assert loaded.importance == 0.9

    by_kind = store.list_memories(kind="preference")
    assert [item.id for item in by_kind] == [record.id]
    assert store.list_memories(kind="project") == []


def test_dedup_merges_near_identical_content(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite", dedup=True)
    first = store.add_memory("User prefers concise answers", tags=["style"])
    duplicate = store.add_memory("user prefers concise answers!", tags=["tone"])

    assert duplicate.id == first.id
    assert len(store.list_memories()) == 1
    assert set(duplicate.tags) == {"style", "tone"}


def test_dedup_disabled_keeps_both(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite")
    store.add_memory("User prefers concise answers")
    store.add_memory("User prefers concise answers")
    assert len(store.list_memories()) == 2


def test_search_prefers_higher_importance(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite")
    low = store.add_memory("docker deployment tips", importance=0.1)
    high = store.add_memory("docker deployment notes", importance=0.9)

    ranked = store.search_memories("docker deployment", limit=2)
    assert [record.id for record in ranked] == [high.id, low.id]


def test_decay_downgrades_stale_memories(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite", dedup=False)
    stale = store.add_memory("kubernetes rollout guide", importance=0.9)
    fresh = store.add_memory("kubernetes rollout walkthrough", importance=0.9)

    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE memories SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", stale.id),
        )

    ranked = store.search_memories("kubernetes rollout", limit=2, decay_halflife_days=30)
    assert ranked[0].id == fresh.id


def test_core_blocks_crud(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite")
    store.set_block("persona", "Concise and blunt")
    store.set_block("goals", "Ship Airgent")

    assert [block.key for block in store.list_blocks()] == ["goals", "persona"]
    store.set_block("persona", "Concise, friendly")
    assert store.get_block("persona").value == "Concise, friendly"
    assert store.delete_block("goals") is True
    assert store.get_block("goals") is None


def test_trace_events_roundtrip(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "db.sqlite")
    store.append_trace_event(kind="turn_start", session_id="s1", payload={"input": "hi"})
    events = store.list_trace_events(session_id="s1")
    assert events[0]["kind"] == "turn_start"
    assert events[0]["payload"] == {"input": "hi"}


def test_auto_extract_multilingual_patterns() -> None:
    extracted = extract_memories("记住每周一开站会。我喜欢简洁的回答,I prefer uv for python envs")
    kinds = {item.kind for item in extracted}
    assert "fact" in kinds
    assert "preference" in kinds

    profile = extract_memories("我叫小明")
    assert profile and profile[0].kind == "profile" and profile[0].content == "小明"

    project = extract_memories("我正在开发一个 agent 框架")
    assert project and project[0].kind == "project"

    assert extract_memories("今天天气怎么样?") == []
