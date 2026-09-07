"""The documentation shipped inside the dashboard.

Docs live here as structured data rather than as Markdown files for one
reason: they are rendered by the same component library as the rest of the
application, so a note, a table and a code block look like Janus rather than
like a README pasted into a panel. A Markdown pipeline would need a parser, a
sanitiser and a second set of styles to reach the same place.

The trade is that authoring is a little more verbose. That is worth paying for
a fixed set of pages that ship with the product; it would not be worth paying
for a wiki.

Every page is checked by `tests/test_docs.py`: slugs unique, sections known,
internal links resolving. A docs site that quietly 404s is worse than none.
"""

from app.docs.content import (
    DOCS,
    SECTIONS,
    doc_by_slug,
    navigation,
    neighbours,
    search,
)

__all__ = ["DOCS", "SECTIONS", "doc_by_slug", "navigation", "neighbours", "search"]
