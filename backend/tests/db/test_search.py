"""Hybrid retrieval: modes, filters, tenant isolation and ranking quality."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_embedder, get_fetcher
from app.providers.embeddings import EmbeddingBatch, EmbeddingError
from app.retrieval.metrics import mean, recall_at_k, reciprocal_rank
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, register_user
from tests.support.fake_web import FakeWeb

pytestmark = [pytest.mark.db, pytest.mark.api]

# One fact per document, so each source maps to exactly one chunk.
LAPTOP_DOCS = {
    "battery": "The Acme L14 battery is rated 70 Wh and lasts up to 18 hours of video playback.",
    "memory": "Memory on the Acme L14 is 16 GB LPDDR5, soldered to the board and not upgradeable.",
    "ports": "Ports include two Thunderbolt 4 USB-C connectors, HDMI 2.1 and a headphone jack.",
    "warranty": "Acme covers the L14 with a one year limited hardware warranty against defects.",
    "display": "The 14 inch OLED display has a 120 Hz refresh rate and 500 nits peak brightness.",
}
HEADPHONE_DOCS = {
    "anc": "The Sono H9 headphones use hybrid active noise cancellation with eight microphones.",
    "codec": "Bluetooth 5.3 on the Sono H9 supports the LDAC and AAC audio codecs.",
    "weight": "The Sono H9 weighs 250 grams and folds flat into the included travel case.",
}

# (query, key of the only relevant document)
EVAL_QUERIES = [
    ("how long does the battery last", "battery"),
    ("is the RAM upgradeable", "memory"),
    ("which connectors and ports does it have", "ports"),
    ("warranty coverage for hardware defects", "warranty"),
    ("screen refresh rate and brightness", "display"),
    ("noise cancelling microphones", "anc"),
    ("supported bluetooth codecs", "codec"),
    ("how heavy are the headphones", "weight"),
]


async def _product(api: httpx.AsyncClient, user: ApiUser, brand: str) -> str:
    response = await api.post(
        "/api/v1/products",
        json={"brand": f"{brand}-{uuid.uuid4().hex[:6]}", "name": "X", "category": "laptop"},
        headers=user.headers,
    )
    assert response.status_code == 201, response.text
    product_id: str = response.json()["id"]
    return product_id


async def _workspace(api: httpx.AsyncClient, user: ApiUser, *product_ids: str) -> str:
    ws: str = (
        await api.post("/api/v1/workspaces", json={"name": "Compare"}, headers=user.headers)
    ).json()["id"]
    for pid in product_ids:
        added = await api.post(
            f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
        )
        assert added.status_code == 201, added.text
    return ws


async def _doc(
    api: httpx.AsyncClient,
    user: ApiUser,
    product_id: str,
    text: str,
    *,
    workspace_id: str | None = None,
    source_type: str = "SPECIFICATION_SHEET",
) -> str:
    data: dict[str, Any] = {"source_type": source_type}
    if workspace_id:
        data["workspace_id"] = workspace_id
    upload = await api.post(
        f"/api/v1/products/{product_id}/sources/upload",
        files={"file": ("doc.txt", text.encode(), "text/plain")},
        data=data,
        headers=user.headers,
    )
    assert upload.status_code == 201, upload.text
    source_id: str = upload.json()["source"]["id"]
    embedded = await api.post(f"/api/v1/sources/{source_id}/embed", headers=user.headers)
    assert embedded.status_code == 200, embedded.text
    return source_id


async def _search(
    api: httpx.AsyncClient, user: ApiUser, ws: str, query: str, **body: Any
) -> httpx.Response:
    return await api.post(
        f"/api/v1/workspaces/{ws}/search", json={"query": query, **body}, headers=user.headers
    )


class Corpus:
    def __init__(self, user: ApiUser, ws: str, laptop: str, phones: str, sources: dict[str, str]):
        self.user, self.ws, self.laptop, self.phones, self.sources = (
            user,
            ws,
            laptop,
            phones,
            sources,
        )


@pytest.fixture
async def corpus(api: httpx.AsyncClient) -> Corpus:
    user = await register_user(api)
    laptop = await _product(api, user, "Acme")
    phones = await _product(api, user, "Sono")
    ws = await _workspace(api, user, laptop, phones)
    sources = {key: await _doc(api, user, laptop, text) for key, text in LAPTOP_DOCS.items()}
    sources |= {key: await _doc(api, user, phones, text) for key, text in HEADPHONE_DOCS.items()}
    return Corpus(user, ws, laptop, phones, sources)


async def test_hybrid_search_returns_cited_chunks(api: httpx.AsyncClient, corpus: Corpus) -> None:
    response = await _search(api, corpus.user, corpus.ws, "battery video playback hours")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "hybrid"
    assert body["embedding_model"] == "hashing-v1"
    assert body["degraded"] is False
    top = body["items"][0]
    assert top["source_id"] == corpus.sources["battery"]
    assert top["lexical_rank"] == 1
    assert top["vector_rank"] == 1
    assert 0 < top["similarity"] <= 1
    assert top["text"].startswith("The Acme L14 battery")
    assert (top["char_start"], top["char_end"]) == (0, len(LAPTOP_DOCS["battery"]))
    assert top["authority"] == "USER"
    scores = [item["score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)


async def test_lexical_and_vector_modes(api: httpx.AsyncClient, corpus: Corpus) -> None:
    lexical = (await _search(api, corpus.user, corpus.ws, "LDAC", mode="lexical")).json()
    assert [i["source_id"] for i in lexical["items"]] == [corpus.sources["codec"]]
    assert lexical["embedding_model"] is None
    assert lexical["items"][0]["vector_rank"] is None

    vector = (await _search(api, corpus.user, corpus.ws, "LDAC", mode="vector", limit=3)).json()
    assert len(vector["items"]) == 3  # vector search always returns nearest neighbours
    assert vector["items"][0]["source_id"] == corpus.sources["codec"]
    assert all(i["lexical_rank"] is None for i in vector["items"])


async def test_websearch_syntax_and_no_match(api: httpx.AsyncClient, corpus: Corpus) -> None:
    phrase = await _search(api, corpus.user, corpus.ws, '"noise cancellation"', mode="lexical")
    assert [i["source_id"] for i in phrase.json()["items"]] == [corpus.sources["anc"]]
    negated = await _search(api, corpus.user, corpus.ws, "acme -warranty", mode="lexical")
    ids = {i["source_id"] for i in negated.json()["items"]}
    assert corpus.sources["warranty"] not in ids
    assert corpus.sources["battery"] in ids
    nothing = await _search(api, corpus.user, corpus.ws, "zeppelin", mode="lexical")
    assert nothing.json()["items"] == []


async def test_filters(api: httpx.AsyncClient, corpus: Corpus) -> None:
    only_phones = await _search(
        api, corpus.user, corpus.ws, "weight battery", product_ids=[corpus.phones], limit=50
    )
    items = only_phones.json()["items"]
    assert items
    assert {i["product_id"] for i in items} == {corpus.phones}

    one_source = await _search(
        api, corpus.user, corpus.ws, "acme", source_ids=[corpus.sources["display"]]
    )
    assert {i["source_id"] for i in one_source.json()["items"]} == {corpus.sources["display"]}

    official = await _search(api, corpus.user, corpus.ws, "battery", authorities=["OFFICIAL"])
    assert official.json()["items"] == []
    reviews = await _search(api, corpus.user, corpus.ws, "battery", source_types=["REVIEW"])
    assert reviews.json()["items"] == []


async def test_workspace_isolation(api: httpx.AsyncClient, corpus: Corpus) -> None:
    # Another tenant adds the same laptop and a private note mentioning a secret term.
    rival = await register_user(api)
    rival_ws = await _workspace(api, rival, corpus.laptop)
    secret = await _doc(
        api,
        rival,
        corpus.laptop,
        "Confidential zanzibar pricing for the L14.",
        workspace_id=rival_ws,
    )
    for mode in ("hybrid", "lexical", "vector"):
        mine = await _search(api, corpus.user, corpus.ws, "zanzibar pricing", mode=mode, limit=50)
        assert secret not in {i["source_id"] for i in mine.json()["items"]}, mode

    # The rival sees their private note plus the shared sources of the laptop only.
    theirs = (await _search(api, rival, rival_ws, "zanzibar", mode="lexical")).json()["items"]
    assert [i["source_id"] for i in theirs] == [secret]
    shared = (await _search(api, rival, rival_ws, "acme", limit=50)).json()["items"]
    assert {i["product_id"] for i in shared} == {corpus.laptop}

    # Shared sources of products NOT in the workspace are excluded too.
    other_product = await _product(api, corpus.user, "Other")
    stray = await _doc(api, corpus.user, other_product, "Unrelated zanzibar blender manual.")
    mine = await _search(api, corpus.user, corpus.ws, "zanzibar", limit=50)
    assert stray not in {i["source_id"] for i in mine.json()["items"]}

    # Non-members get 404, not an empty result.
    assert (await _search(api, rival, corpus.ws, "battery")).status_code == 404
    unknown = await _search(api, corpus.user, str(uuid.uuid4()), "battery")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "workspace_not_found"


async def test_private_workspace_source_is_searchable_by_members(
    api: httpx.AsyncClient, corpus: Corpus
) -> None:
    note = await _doc(
        api,
        corpus.user,
        corpus.laptop,
        "Team note: the keyboard backlight flickers on some units.",
        workspace_id=corpus.ws,
        source_type="USER_DOCUMENT",
    )
    hits = (await _search(api, corpus.user, corpus.ws, "keyboard backlight")).json()["items"]
    assert hits[0]["source_id"] == note
    assert hits[0]["workspace_id"] == corpus.ws


async def test_only_latest_document_version_is_searched(
    api_app: FastAPI, api: httpx.AsyncClient, corpus: Corpus, make_settings: SettingsFactory
) -> None:
    web = FakeWeb()
    web.host("acme.example", "93.184.216.34")
    web.page("acme.example", "/l14", "<html><body>Warranty: one year quokka plan.</body></html>")
    fetcher = web.fetcher(make_settings())
    api_app.dependency_overrides[get_fetcher] = lambda: fetcher
    source = await api.post(
        f"/api/v1/products/{corpus.laptop}/sources",
        json={
            "source_type": "MANUFACTURER_PAGE",
            "title": "Official page",
            "url": "https://acme.example/l14",
            "authority": "OFFICIAL",
        },
        headers=corpus.user.headers,
    )
    sid = source.json()["id"]

    async def ingest_and_embed() -> None:
        ingested = await api.post(f"/api/v1/sources/{sid}/ingest", headers=corpus.user.headers)
        assert ingested.status_code == 200, ingested.text
        embedded = await api.post(f"/api/v1/sources/{sid}/embed", headers=corpus.user.headers)
        assert embedded.status_code == 200, embedded.text

    await ingest_and_embed()
    first = await _search(api, corpus.user, corpus.ws, "quokka", source_ids=[sid])
    assert "one year" in first.json()["items"][0]["text"]

    web.page("acme.example", "/l14", "<html><body>Warranty: three year quokka plan.</body></html>")
    await ingest_and_embed()
    for mode in ("hybrid", "lexical", "vector"):
        items = (
            await _search(api, corpus.user, corpus.ws, "quokka", mode=mode, source_ids=[sid])
        ).json()["items"]
        assert len(items) == 1, mode
        assert "three year" in items[0]["text"], mode
    official = await _search(api, corpus.user, corpus.ws, "warranty", authorities=["OFFICIAL"])
    assert {i["source_id"] for i in official.json()["items"]} == {sid}


async def test_embedding_outage_degrades_hybrid_and_fails_vector(
    api_app: FastAPI, api: httpx.AsyncClient, corpus: Corpus
) -> None:
    class BrokenEmbedder:
        model = "hashing-v1"
        dimensions = 1536

        async def embed(self, texts: list[str]) -> EmbeddingBatch:
            raise EmbeddingError("provider down")

        async def aclose(self) -> None:
            return None

    api_app.dependency_overrides[get_embedder] = BrokenEmbedder
    try:
        hybrid = (await _search(api, corpus.user, corpus.ws, "battery")).json()
        assert hybrid["degraded"] is True
        assert hybrid["embedding_model"] is None
        assert hybrid["items"][0]["source_id"] == corpus.sources["battery"]
        vector = await _search(api, corpus.user, corpus.ws, "battery", mode="vector")
        assert vector.status_code == 503
        assert vector.json()["error"]["code"] == "search_unavailable"
    finally:
        api_app.dependency_overrides.pop(get_embedder)


async def test_retrieval_quality_on_labelled_queries(
    api: httpx.AsyncClient, corpus: Corpus
) -> None:
    """Recall@K and MRR per mode on a small labelled set (offline hashing embedder)."""
    report: dict[str, dict[str, float]] = {}
    for mode in ("lexical", "vector", "hybrid"):
        recalls: list[float] = []
        reciprocal_ranks: list[float] = []
        for query, key in EVAL_QUERIES:
            items = (await _search(api, corpus.user, corpus.ws, query, mode=mode)).json()["items"]
            ranked = [i["source_id"] for i in items]
            relevant = {corpus.sources[key]}
            recalls.append(recall_at_k(ranked, relevant, 3))
            reciprocal_ranks.append(reciprocal_rank(ranked, relevant))
        report[mode] = {"recall@3": mean(recalls), "mrr": mean(reciprocal_ranks)}
    print(f"\nretrieval quality: {report}")  # noqa: T201
    assert report["hybrid"]["recall@3"] >= report["lexical"]["recall@3"]
    assert report["hybrid"]["recall@3"] >= 0.75
