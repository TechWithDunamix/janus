"""The block vocabulary a documentation page is written in.

Small and closed on purpose. Eight block types cover every page Janus ships,
and a closed set means the renderer is exhaustive — there is no branch that
falls through to "unknown block", and adding a ninth is a deliberate act in
two files rather than something that silently renders nothing.
"""

from __future__ import annotations

from typing import Any

__all__ = ["code", "columns", "heading", "note", "para", "steps", "table", "terms"]


def para(text: str) -> dict[str, Any]:
    """A paragraph. Inline `code` spans are written with backticks."""
    return {"type": "p", "text": text}


def heading(text: str, level: int = 2) -> dict[str, Any]:
    """A section heading. Level 2 gets an anchor in the on-page contents."""
    return {"type": "h", "text": text, "level": level}


def code(source: str, lang: str = "bash", caption: str = "") -> dict[str, Any]:
    return {"type": "code", "lang": lang, "code": source.strip("\n"), "caption": caption}


def steps(items: list[str], ordered: bool = False) -> dict[str, Any]:
    return {"type": "list", "items": items, "ordered": ordered}


def note(text: str, *, tone: str = "info", title: str = "") -> dict[str, Any]:
    """A callout. `tone` is one of info, caution, critical, ok."""
    return {"type": "note", "tone": tone, "title": title, "text": text}


def table(head: list[str], rows: list[list[str]]) -> dict[str, Any]:
    return {"type": "table", "head": head, "rows": rows}


def terms(items: list[tuple[str, str]]) -> dict[str, Any]:
    """A definition list — a term and what it means."""
    return {"type": "terms", "items": [{"term": t, "text": d} for t, d in items]}


def columns(items: list[tuple[str, str]]) -> dict[str, Any]:
    """A grid of small titled cards, for an overview page."""
    return {"type": "cards", "items": [{"title": t, "text": d} for t, d in items]}
