"""Regression checks for Python source shipped to the managed host."""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

from custom_components.pi_manager.const import HELPER_VERSION

REMOTE_DIR = Path(__file__).parents[1] / "custom_components" / "pi_manager" / "remote"
REMOTE_PYTHON_FILES = ("pi_manager_agent.py", "pi-managerctl")


def _significant_tokens(source: str) -> list[tokenize.TokenInfo]:
    ignored = {
        tokenize.COMMENT,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NL,
        tokenize.NEWLINE,
    }
    return [token for token in tokenize.generate_tokens(io.StringIO(source).readline) if token.type not in ignored]


def _has_unparenthesized_multiple_exception(source: str) -> bool:
    """Detect Python 3.14-only ``except A, B:`` syntax without parsing it."""

    tokens = _significant_tokens(source)
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string != "except":
            continue

        depth = 0
        for candidate in tokens[index + 1 :]:
            if candidate.string in "([{":
                depth += 1
            elif candidate.string in ")]}":
                depth -= 1
            elif candidate.string == ":" and depth == 0:
                break
            elif candidate.string == "," and depth == 0:
                return True
    return False


def test_remote_python_files_are_compatible_with_python_313() -> None:
    for filename in REMOTE_PYTHON_FILES:
        source = (REMOTE_DIR / filename).read_text(encoding="utf-8")
        assert not _has_unparenthesized_multiple_exception(source), filename


def test_remote_sources_compile_as_python() -> None:
    for filename in REMOTE_PYTHON_FILES:
        path = REMOTE_DIR / filename
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def test_remote_agent_marker_matches_shared_helper_version() -> None:
    source = (REMOTE_DIR / "pi_manager_agent.py").read_text(encoding="utf-8")
    assert f'AGENT_VERSION = "{HELPER_VERSION}"' in source
