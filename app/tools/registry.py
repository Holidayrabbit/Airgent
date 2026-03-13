from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tools.memory_tools import remember_note, search_memory
from app.tools.skill_tools import list_skills, load_skill


@dataclass(frozen=True)
class ToolDefinition:
    key: str
    implementation: Any


class ToolRegistry:
    def __init__(self) -> None:
        self._registry = {
            "search_memory": ToolDefinition("search_memory", search_memory),
            "remember_note": ToolDefinition("remember_note", remember_note),
            "list_skills": ToolDefinition("list_skills", list_skills),
            "load_skill": ToolDefinition("load_skill", load_skill),
        }

    def get(self, key: str) -> ToolDefinition:
        return self._registry[key]
