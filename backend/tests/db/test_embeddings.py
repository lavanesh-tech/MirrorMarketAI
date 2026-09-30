"""Chunking + embedding jobs, pgvector storage and the worker queue."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database
from app.models.retrieval import ChunkEmbedding, DocumentChunk, EmbeddingJob, JobStatus
from app.providers.embeddings import EmbeddingBatch, HashingEmbeddingProvider
from app.services.embeddings import EmbeddingService
from app.workers.embedding_worker import drain, run_worker
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, migrate, register_user

pytestmark = pytest.mark.db

SPEC_TEXT = "\n\n".join(
    [
        "Acme Laptop 14 technical specifications.",
        "Battery: 70 Wh lithium-polymer. Up to 18 hours of video playback.",
        "Memory: 16 GB LPDDR5 soldered, not upgradeable.",
        "Ports: two USB-C with Thunderbolt 4, one HDMI 2.1, headphone jack.",
        "Warranty: one year limited hardware warranty; extended plans available.",
    ]
    * 15
)


async def _ingested_source(api: httpx.AsyncClient, user: ApiUser) -> str:
    pid = (
        await api.post(
            "/api/v1/products",
            json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "L14", "category": "laptop"},
            headers=user.headers,
        )
    ).json()["id"]
    upload = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("specs.txt", SPEC_TEXT.encode(), "text/plain")},
        data={"source_type": "SPECIFICATION_SHEET"},
        headers=user.headers,
    )
    assert upload.status_code == 201, upload.text
    source_id: str = upload.json()["source"]["id"]
    return source_id


@pytest.mark.api
async def test_ingestion_enqueues_job_and_embed_runs_it(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    sid = await _ingested_source(api, user)

    pending = await db_session.scalar(select(func.count()).select_from(EmbeddingJob))
    assert pending == 1  # created in the same transaction as the document

    response = await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "SUCCEEDED"
    assert job["model"] == "hashing-v1"
    assert job["chunk_count"] >= 2
    assert job["embedded_count"] == job["chunk_count"]
    assert job["attempts"] == 1

    chunks = (await api.get(f"/api/v1/sources/{sid}/chunks?limit=100", headers=user.headers)).json()
    assert chunks["page"]["total"] == job["chunk_count"]
    assert [c["chunk_index"] for c in chunks["items"]] == list(range(job["chunk_count"]))

    dims = await db_session.scalar(
        text("SELECT vector_dims(embedding) FROM chunk_embeddings LIMIT 1")
    )
    assert dims == 1536

    fetched = await api.get(f"/api/v1/embedding-jobs/{job['id']}", headers=user.headers)
    assert fetched.json()["id"] == job["id"]


@pytest.mark.api
async def test_embedding_is_idempotent(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    user = await register_user(api)
    sid = await _ingested_source(api, user)
    first = (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).json()
    second = (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).json()
    assert second["id"] == first["id"]
    embeddings = await db_session.scalar(select(func.count()).select_from(ChunkEmbedding))
    assert embeddings == first["chunk_count"]


@pytest.mark.api
async def test_nearest_neighbour_query_finds_the_relevant_chunk(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    sid = await _ingested_source(api, user)
    await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)

    query = (
        await HashingEmbeddingProvider().embed(["how many hours of battery playback"])
    ).vectors[0]
    best = await db_session.scalar(
        select(DocumentChunk.text)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(DocumentChunk.source_id == uuid.UUID(sid))
        .order_by(ChunkEmbedding.embedding.cosine_distance(query))
        .limit(1)
    )
    assert best is not None
    assert "Battery" in best


@pytest.mark.api
async def test_embed_permissions_and_visibility(api: httpx.AsyncClient) -> None:
    owner, other = await register_user(api), await register_user(api)
    sid = await _ingested_source(api, owner)
    assert (
        await api.post(f"/api/v1/sources/{sid}/embed", headers=other.headers)
    ).status_code == 403
    assert (
        await api.get(f"/api/v1/embedding-jobs/{uuid.uuid4()}", headers=owner.headers)
    ).status_code == 404


class _FlakyProvider(HashingEmbeddingProvider):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("provider unavailable")
        return await super().embed(texts)


async def _job_for_new_document(
    session: AsyncSession, api: httpx.AsyncClient, user: ApiUser
) -> EmbeddingJob:
    await _ingested_source(api, user)
    job = await session.scalar(select(EmbeddingJob).order_by(EmbeddingJob.created_at.desc()))
    assert job is not None
    return job


@pytest.mark.api
async def test_failed_job_is_retried_then_marked_failed(
    api: httpx.AsyncClient, db_session: AsyncSession, make_settings: SettingsFactory
) -> None:
    settings = make_settings(embedding_job_max_attempts=2)
    user = await register_user(api)
    await _job_for_new_document(db_session, api, user)

    service = EmbeddingService(db_session, settings, _FlakyProvider(failures=10))
    first = await service.claim_next()
    assert first is not None
    after_first = await service.run(first)
    assert after_first.status == JobStatus.PENDING.value  # will be retried
    assert "provider unavailable" in (after_first.last_error or "")

    second = await service.claim_next()
    assert second is not None
    after_second = await service.run(second)
    assert after_second.status == JobStatus.FAILED.value
    assert after_second.attempts == 2
    assert await service.claim_next() is None


@pytest.mark.api
async def test_transient_failure_then_success(
    api: httpx.AsyncClient, db_session: AsyncSession, make_settings: SettingsFactory
) -> None:
    user = await register_user(api)
    await _job_for_new_document(db_session, api, user)
    service = EmbeddingService(db_session, make_settings(), _FlakyProvider(failures=1))
    for _ in range(2):
        job = await service.claim_next()
        assert job is not None
        result = await service.run(job)
    assert result.status == JobStatus.SUCCEEDED.value
    assert result.attempts == 2


@pytest.fixture
def worker_db_url(fresh_database_url: Callable[[], str]) -> str:
    url = fresh_database_url()
    migrate(url)
    return url


async def test_parallel_workers_never_process_the_same_job(
    worker_db_url: str, make_settings: SettingsFactory
) -> None:
    """Real commits on a dedicated database: SKIP LOCKED gives each job to one worker."""
    settings: Settings = make_settings(database_url=worker_db_url)
    database = Database.from_settings(settings)
    try:
        async with database.session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, display_name) "
                    "VALUES (:u, 'w@example.com', 'x', 'W')"
                ),
                {"u": (user_id := uuid.uuid4())},
            )
            await session.execute(
                text(
                    "INSERT INTO products "
                    "(id, brand, name, category, canonical_key, created_by_id) "
                    "VALUES (:p, 'B', 'N', 'laptop', 'b::n', :u)"
                ),
                {"p": (product_id := uuid.uuid4()), "u": user_id},
            )
            for n in range(6):
                source_id, snap_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO product_sources "
                        "(id, product_id, created_by_id, source_type, authority, title, status) "
                        "VALUES (:s, :p, :u, 'OTHER', 'USER', 't', 'INGESTED')"
                    ),
                    {"s": source_id, "p": product_id, "u": user_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO source_snapshots "
                        "(id, source_id, content_type, byte_size, sha256, raw_content) "
                        "VALUES (:i, :s, 'text/plain', 1, :h, 'x')"
                    ),
                    {"i": snap_id, "s": source_id, "h": f"{n:064d}"},
                )
                await session.execute(
                    text(
                        "INSERT INTO source_documents "
                        "(id, snapshot_id, source_id, product_id, text, char_count, parser) "
                        "VALUES (:d, :i, :s, :p, :t, 20, 'text')"
                    ),
                    {
                        "d": doc_id,
                        "i": snap_id,
                        "s": source_id,
                        "p": product_id,
                        "t": f"Document {n}. Battery 70 Wh.",
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO embedding_jobs (id, document_id, model, status) "
                        "VALUES (:j, :d, 'hashing-v1', 'PENDING')"
                    ),
                    {"j": uuid.uuid4(), "d": doc_id},
                )
            await session.commit()

        provider = HashingEmbeddingProvider()
        counts = await asyncio.gather(*(drain(database, settings, provider) for _ in range(3)))
        assert sum(counts) == 6

        async with database.session_factory() as session:
            statuses = list(await session.scalars(select(EmbeddingJob.status)))
            attempts = list(await session.scalars(select(EmbeddingJob.attempts)))
        assert statuses == ["SUCCEEDED"] * 6
        assert attempts == [1] * 6  # nobody processed a job twice

        # The CLI entry point drains an empty queue and exits cleanly.
        assert await run_worker(settings, once=True) == 0
    finally:
        await database.dispose()
