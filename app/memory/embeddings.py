"""Embedding client for hybrid memory retrieval.

Talks to an OpenAI-compatible `/embeddings` endpoint (the same proxy used for
the model, via OPENAI_BASE_URL + OPENAI_API_KEY) using only the stdlib. Used
by the memory store to embed memories and queries when hybrid retrieval is
enabled (AIRGENT_MEMORY_EMBEDDINGS=1); every failure degrades to keyword-only
search instead of breaking the run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


class EmbeddingError(Exception):
    pass


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        post: Callable[..., tuple[int, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._post = post or self._http_post
        self._dimension: int | None = None

    # ------------------------------------------------------------------ http
    @staticmethod
    def _http_post(url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, str]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")

    # ------------------------------------------------------------------- api
    @property
    def dimension(self) -> int | None:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input, in order."""

        if not texts:
            return []
        payload = json.dumps(
            {"model": self.model, "input": texts},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/embeddings"
        try:
            status, raw = self._post(url, headers=headers, body=payload, timeout=self.timeout)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise EmbeddingError(f"embeddings request failed: {exc}") from exc
        if status != 200:
            raise EmbeddingError(f"embeddings endpoint returned HTTP {status}")
        try:
            document = json.loads(raw)
            data = document["data"]
            vectors = [item["embedding"] for item in data]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EmbeddingError(f"malformed embeddings response: {exc}") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embeddings returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        if vectors and self._dimension is None:
            self._dimension = len(vectors[0])
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
