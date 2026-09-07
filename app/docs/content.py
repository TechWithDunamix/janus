"""The documentation pages.

Content lives in `app/docs/pages/`, one module per section, because a single
file holding every page was becoming one nobody would open. This module
assembles them and provides the lookups.
"""

from __future__ import annotations

from typing import Any

from app.docs.pages import gateway, observability, operations, security, start

__all__ = ["DOCS", "SECTIONS", "doc_by_slug", "navigation", "neighbours", "search"]

#: Section order, which is also the order of the docs sidebar and of reading.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("start", "Getting started"),
    ("gateway", "Gateway"),
    ("security", "Security"),
    ("observability", "Observability"),
    ("operations", "Operations"),
)

DOCS: tuple[dict[str, Any], ...] = (
    *start.PAGES,
    *gateway.PAGES,
    *security.PAGES,
    *observability.PAGES,
    *operations.PAGES,
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def doc_by_slug(slug: str) -> dict[str, Any] | None:
    return next((doc for doc in DOCS if doc["slug"] == slug), None)


def navigation() -> list[dict[str, Any]]:
    """The docs sidebar: sections in order, each with its pages in order."""
    return [
        {
            "key": key,
            "label": label,
            "pages": [
                {"slug": d["slug"], "title": d["title"], "summary": d["summary"]}
                for d in DOCS
                if d["section"] == key
            ],
        }
        for key, label in SECTIONS
    ]


def neighbours(slug: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """The previous and next page, in reading order.

    Reading order is the section order, not the tuple order, so a page added to
    an earlier section lands where a reader expects rather than at the end.
    """
    ordered = [d for key, _ in SECTIONS for d in DOCS if d["section"] == key]
    index = next((i for i, d in enumerate(ordered) if d["slug"] == slug), None)
    if index is None:
        return None, None

    def brief(doc: dict[str, Any]) -> dict[str, str]:
        return {"slug": doc["slug"], "title": doc["title"]}

    return (
        brief(ordered[index - 1]) if index > 0 else None,
        brief(ordered[index + 1]) if index + 1 < len(ordered) else None,
    )


def search(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Substring search over titles, summaries and body text.

    Deliberately not an index. Fifteen pages is small enough that scanning them
    is faster than maintaining something that can fall out of date, and a
    search that returns a stale result is worse than one that is slow.
    """
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        return []

    hits: list[tuple[int, dict[str, Any]]] = []
    for doc in DOCS:
        haystack = " ".join(
            [doc["title"], doc["summary"], *_text_of(doc)]
        ).lower()
        if needle not in haystack:
            continue
        # Title matches rank above body matches; nobody searching "routes"
        # wants the troubleshooting page first.
        score = 0 if needle in doc["title"].lower() else 1 if needle in doc["summary"].lower() else 2
        hits.append((score, {"slug": doc["slug"], "title": doc["title"], "summary": doc["summary"]}))

    hits.sort(key=lambda pair: pair[0])
    return [doc for _, doc in hits[:limit]]


def _text_of(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for block in doc["blocks"]:
        if "text" in block:
            out.append(str(block["text"]))
        if "code" in block:
            out.append(str(block["code"]))
        for item in block.get("items", []):
            out.append(item if isinstance(item, str) else " ".join(str(v) for v in item.values()))
        for row in block.get("rows", []):
            out.extend(str(cell) for cell in row)
    return out
