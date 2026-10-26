"""Automatic memory extraction (mem0-style pipeline, heuristic stage).

After each user turn, eligible sentences are mined for durable facts —
preferences, profile details, projects, explicit "remember this" requests.
The default extractor is a deterministic multilingual pattern matcher so the
feature needs no extra model calls; swap in an LLM-backed extractor later by
replacing `extract_memories`.

Enabled with AIRGENT_MEMORY_AUTO_EXTRACT=1 (off by default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (kind, importance, pattern) — first match wins per sentence.
_PATTERNS: tuple[tuple[str, float, re.Pattern[str]], ...] = (
    # Explicit remember requests — highest value.
    ("fact", 0.8, re.compile(r"(?:请?记住[:：,，]?\s*|记一下[:：,，]?\s*|remember that\s+|remember:\s*)(.{4,})", re.IGNORECASE)),
    # Profile facts.
    (
        "profile",
        0.9,
        re.compile(r"(?:我叫|我的名字是|我的邮箱是|my name is|i am called)\s*([\w\u4e00-\u9fff@.\-]{1,64})", re.IGNORECASE),
    ),
    # Preferences.
    (
        "preference",
        0.7,
        re.compile(r"(?:我(?:喜欢|偏好|习惯|更愿意)|(?:i\s+)(?:prefer|like|usually))\s*([^。;；!！?？\n]{2,160})", re.IGNORECASE),
    ),
    # Ongoing projects / commitments.
    (
        "project",
        0.6,
        re.compile(r"(?:我(?:正在|在)(?:做|开发|写|研究)|(?:i'm|i am)\s+working on)\s*([^。;；!！?？\n]{2,160})", re.IGNORECASE),
    ),
)

_NOISE = re.compile(r"^[\s,，。.、;；:：!！?？\")」』]+|[\s,，。.、;；:：\"(「『]+$")


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    content: str
    importance: float


def extract_memories(user_input: str) -> list[ExtractedMemory]:
    """Mine durable facts from one user message (deduplicated, order kept)."""

    extracted: list[ExtractedMemory] = []
    seen: set[str] = set()
    for sentence in re.split(r"[。.!！?？\n]+", user_input):
        stripped = sentence.strip()
        if not stripped or len(stripped) < 4:
            continue
        for kind, importance, pattern in _PATTERNS:
            match = pattern.search(stripped)
            if match is None:
                continue
            content = _NOISE.sub("", match.group(1)).strip()
            if not content or content.lower() in seen:
                break
            seen.add(content.lower())
            extracted.append(ExtractedMemory(kind=kind, content=content, importance=importance))
            break
    return extracted[:5]
