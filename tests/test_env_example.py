"""Guard: .env.example is committed, so it must only list key names (CLAUDE.md 보안)."""

from __future__ import annotations

from pathlib import Path

from src.common.config import SECRET_NAMES

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _entries() -> dict[str, str]:
    entries = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            entries[name.strip()] = value.strip()
    return entries


def test_env_example_has_no_secret_values():
    entries = _entries()
    filled = [name for name in SECRET_NAMES if entries.get(name)]
    # Never print the values themselves: only the names.
    assert not filled, (
        f"{filled} have values in .env.example. Put real keys in .env (git-ignored), "
        "remove them here, and rotate the exposed keys."
    )


def test_env_example_lists_every_secret_name():
    assert set(SECRET_NAMES) <= set(_entries())
