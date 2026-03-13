from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.tools.file_tools import _create_text_file, _edit_text_file, _read_text_file, _resolve_project_path


def _wrapper(tmp_path):
    settings = SimpleNamespace(project_root=tmp_path)
    context = SimpleNamespace(settings=settings)
    return SimpleNamespace(context=context)


def test_resolve_project_path_rejects_escape(tmp_path) -> None:
    wrapper = _wrapper(tmp_path)

    with pytest.raises(AppError):
        _resolve_project_path(wrapper.context, "../outside.txt")


def test_file_tools_create_read_and_edit(tmp_path) -> None:
    wrapper = _wrapper(tmp_path)

    created = _create_text_file(wrapper.context, "notes/todo.md", "hello")
    loaded = _read_text_file(wrapper.context, "notes/todo.md")
    edited = _edit_text_file(wrapper.context, "notes/todo.md", "hello", "hello world")
    reloaded = _read_text_file(wrapper.context, "notes/todo.md")

    assert created["status"] == "created"
    assert loaded["content"] == "hello"
    assert edited["status"] == "edited"
    assert reloaded["content"] == "hello world"
