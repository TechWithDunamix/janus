"""The shipped documentation.

A docs site that quietly 404s is worse than none, so these assert the things
that break silently: duplicate slugs, unknown sections, links to pages that do
not exist, and blocks the renderer has no branch for.
"""

from __future__ import annotations

import re

import pytest

from app.docs import DOCS, SECTIONS, doc_by_slug, navigation, neighbours, search
from tests.helpers import make_user, sign_in

#: Every block type `views/ui/docs.tsx` has a branch for. A page using anything
#: else renders as nothing at all, with no error.
RENDERABLE = {"p", "h", "code", "list", "note", "table", "terms", "cards"}

#: Tones `NOTE_TONE` in the renderer knows.
NOTE_TONES = {"info", "caution", "critical", "ok"}


class TestTheContent:
    def test_there_are_at_least_ten_pages(self):
        assert len(DOCS) >= 10

    def test_slugs_are_unique(self):
        slugs = [doc["slug"] for doc in DOCS]
        assert len(slugs) == len(set(slugs))

    def test_slugs_are_url_safe(self):
        for doc in DOCS:
            assert re.fullmatch(r"[a-z0-9-]+", doc["slug"]), doc["slug"]

    def test_every_page_belongs_to_a_known_section(self):
        known = {key for key, _ in SECTIONS}
        for doc in DOCS:
            assert doc["section"] in known, f"{doc['slug']} -> {doc['section']}"

    def test_every_section_has_pages(self):
        for section in navigation():
            assert section["pages"], f"empty section: {section['key']}"

    def test_every_page_has_a_title_and_summary(self):
        for doc in DOCS:
            assert doc["title"].strip()
            assert doc["summary"].strip()

    def test_every_block_is_one_the_renderer_handles(self):
        for doc in DOCS:
            for block in doc["blocks"]:
                assert block["type"] in RENDERABLE, f"{doc['slug']}: {block['type']}"

    def test_every_note_tone_is_one_the_renderer_styles(self):
        for doc in DOCS:
            for block in doc["blocks"]:
                if block["type"] == "note":
                    assert block.get("tone", "info") in NOTE_TONES, doc["slug"]

    def test_every_table_row_matches_its_header(self):
        for doc in DOCS:
            for block in doc["blocks"]:
                if block["type"] != "table":
                    continue
                width = len(block["head"])
                for row in block["rows"]:
                    assert len(row) == width, f"{doc['slug']}: ragged table row {row}"


class TestInternalLinks:
    def test_every_internal_docs_link_resolves(self):
        """A link to a page that does not exist is a 404 nobody notices until a
        reader hits it."""
        broken: list[str] = []
        for doc in DOCS:
            for text in _all_text(doc):
                for href in re.findall(r"\]\((/docs/[a-z0-9-]+)\)", text):
                    if doc_by_slug(href.rsplit("/", 1)[-1]) is None:
                        broken.append(f"{doc['slug']} -> {href}")
        assert not broken, f"broken internal links: {broken}"

    def test_external_links_are_absolute(self):
        for doc in DOCS:
            for text in _all_text(doc):
                for href in re.findall(r"\]\(([^)]+)\)", text):
                    assert href.startswith(("/", "http://", "https://")), href


class TestNavigation:
    def test_reading_order_follows_the_sections(self):
        first = navigation()[0]["pages"][0]["slug"]
        previous, _ = neighbours(first)
        assert previous is None

        last_section = navigation()[-1]["pages"][-1]["slug"]
        _, following = neighbours(last_section)
        assert following is None

    def test_neighbours_chain_through_every_page(self):
        """Walking `next` from the first page must reach every page exactly
        once, or a page is unreachable by reading forward."""
        seen = [navigation()[0]["pages"][0]["slug"]]
        while True:
            _, following = neighbours(seen[-1])
            if following is None:
                break
            seen.append(following["slug"])
        assert len(seen) == len(DOCS)
        assert len(set(seen)) == len(DOCS)

    def test_an_unknown_slug_has_no_neighbours(self):
        assert neighbours("nope") == (None, None)


class TestSearch:
    def test_a_title_match_ranks_first(self):
        results = search("rate limiting")
        assert results[0]["slug"] == "rate-limiting"

    def test_body_text_is_searched(self):
        assert any(r["slug"] == "domains" for r in search("automatic https"))

    def test_a_short_query_returns_nothing(self):
        """Otherwise every keystroke returns the whole manual."""
        assert search("a") == []
        assert search("") == []

    def test_no_match_is_empty(self):
        assert search("kubernetes helm chart") == []


class TestTheRoutes:
    async def test_the_index_renders(self, client):
        user = await make_user(role="Viewer")
        await sign_in(client, user)
        assert (await client.get("/docs")).status_code == 200

    @pytest.mark.parametrize("slug", [doc["slug"] for doc in DOCS])
    async def test_every_page_renders(self, client, slug):
        user = await make_user(role="Viewer")
        await sign_in(client, user)
        assert (await client.get(f"/docs/{slug}")).status_code == 200

    async def test_an_unknown_page_is_a_404(self, client):
        user = await make_user(role="Viewer")
        await sign_in(client, user)
        assert (await client.get("/docs/nonsense")).status_code == 404

    async def test_docs_still_require_signing_in(self, client):
        """No permission is needed, but they are not public: these pages
        describe the estate's own conventions."""
        assert (await client.get("/docs")).status_code == 302

    async def test_search_renders(self, client):
        user = await make_user(role="Viewer")
        await sign_in(client, user)
        response = await client.get("/docs/search", params={"q": "rate"})
        assert response.status_code == 200


def _all_text(doc: dict) -> list[str]:
    out: list[str] = []
    for block in doc["blocks"]:
        if isinstance(block.get("text"), str):
            out.append(block["text"])
        for item in block.get("items", []):
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(str(v) for v in item.values())
        for row in block.get("rows", []):
            out.extend(str(cell) for cell in row)
    return out
