from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tools.file_tools import create_file, edit_file, read_file
from app.tools.memory_tools import remember_note, search_memory
from app.tools.skill_tools import list_skills, load_skill


@dataclass(frozen=True)
class ToolDefinition:
    key: str
    implementation: Any


class ToolRegistry:
    def __init__(self) -> None:
        self._registry = {
            "read_file": ToolDefinition("read_file", read_file),
            "create_file": ToolDefinition("create_file", create_file),
            "edit_file": ToolDefinition("edit_file", edit_file),
            "search_memory": ToolDefinition("search_memory", search_memory),
            "remember_note": ToolDefinition("remember_note", remember_note),
            "list_skills": ToolDefinition("list_skills", list_skills),
            "load_skill": ToolDefinition("load_skill", load_skill),
        }

    def get(self, key: str) -> ToolDefinition:
        return self._registry[key]
