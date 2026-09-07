"""The in-dashboard documentation.

Guarded like every other dashboard page, but with no permission requirement:
documentation a Viewer cannot read is documentation that does not help the
person most likely to need it. Signing in is still required — these pages
describe the estate's own conventions.
"""

from __future__ import annotations

from typing import Any

from sillo.core.http import HttpContext
from sillo.responses import not_found
from sillo_inertia import render

from app.docs import DOCS, doc_by_slug, navigation, neighbours, search
from routes.web._kit import page

__all__ = ["docs_index", "docs_page", "docs_search"]


@page()
async def docs_index(ctx: HttpContext) -> Any:
    return await render(
        "docs/Index",
        {
            "navigation": navigation(),
            "total": len(DOCS),
        },
    )


@page()
async def docs_page(ctx: HttpContext, slug: str) -> Any:
    doc = doc_by_slug(slug)
    if doc is None:
        return not_found()

    previous, following = neighbours(slug)
    return await render(
        "docs/Page",
        {
            "navigation": navigation(),
            "doc": doc,
            "previous": previous,
            "next": following,
            # Level-2 headings become the on-page contents. Built here rather
            # than in the browser so it is present in the first render.
            "contents": [
                {"text": block["text"], "id": _anchor(block["text"])}
                for block in doc["blocks"]
                if block["type"] == "h" and block.get("level", 2) == 2
            ],
        },
    )


@page()
async def docs_search(ctx: HttpContext) -> Any:
    query = (ctx.query_params.get("q") or "").strip()
    return await render(
        "docs/Search",
        {"navigation": navigation(), "query": query, "results": search(query)},
    )


def _anchor(text: str) -> str:
    """A URL fragment for a heading.

    Mirrors the slug the renderer puts on the heading element; the two are kept
    in one place here so a contents link cannot point at an id that does not
    exist.
    """
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
