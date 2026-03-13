from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def test_settings_read_openai_values_from_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_MODE", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY = test-key",
                "OPENAI_BASE_URL = https://proxy.example.com/v1",
                "OPENAI_API_MODE = chat_completions",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.openai_api_key == "test-key"
    assert settings.openai_base_url == "https://proxy.example.com/v1"
    assert settings.openai_api_mode == "chat_completions"
