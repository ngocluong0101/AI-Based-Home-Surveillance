"""Read and update the local `.env` file without losing its comments or unrelated keys.

Used by the GUI settings panel. `src/config` still reads `.env` once at startup, so
values written here take effect the next time the application starts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

# Same location python-dotenv finds from src/config/__init__.py (project root).
ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"

_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_NEEDS_QUOTES = re.compile(r"[\s#'\"]")


def is_truthy(value: str | None, default: bool = False) -> bool:
    """Interpret a `.env` value the same way src/config does for boolean flags."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    # An unquoted `#` preceded by whitespace starts a comment, as in python-dotenv.
    if " #" in raw:
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def read_env_values(path: Path) -> dict[str, str]:
    """Return the KEY=value pairs in `path`; a missing file gives an empty dict."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            values[match.group(1)] = _parse_value(match.group(2))
    return values


def _format_value(value: str) -> str:
    if "\n" in value or "\r" in value or '"' in value:
        raise ValueError("Values cannot contain line breaks or double quotes")
    if value and _NEEDS_QUOTES.search(value):
        return f'"{value}"'
    return value


def write_env_values(path: Path, updates: Mapping[str, str]) -> None:
    """Set the given keys in `path`, keeping every other line as it was.

    Existing assignments of a key are replaced in place (all of them, if repeated);
    keys not in the file are appended. The file is rewritten through a temporary
    file with mode 0600, because `.env` usually holds bot tokens.
    """
    new_lines = {key: f"{key}={_format_value(value)}" for key, value in updates.items()}
    seen: set[str] = set()

    lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _ASSIGNMENT.match(line)
            if match and match.group(1) in new_lines:
                lines.append(new_lines[match.group(1)])
                seen.add(match.group(1))
            else:
                lines.append(line)

    lines.extend(line for key, line in new_lines.items() if key not in seen)

    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp_path, path)
