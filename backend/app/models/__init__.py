"""SQLAlchemy ORM models (PostgreSQL tables).

Import every model module here so `Base.metadata` is complete for Alembic
autogenerate and the schema-drift test.
"""

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.catalog import (
    Product,
    ProductIdentifier,
    ProductSpecification,
    ProductVariant,
    WorkspaceProduct,
)
from app.models.identity import (
    ComparisonWorkspace,
    Organization,
    OrganizationMember,
    User,
    WorkspaceMember,
)
from app.models.retrieval import ChunkEmbedding, DocumentChunk, EmbeddingJob
from app.models.sources import ProductSource, SourceDocument, SourceSnapshot

__all__ = [
    "Base",
    "ChunkEmbedding",
    "ComparisonWorkspace",
    "DocumentChunk",
    "EmbeddingJob",
    "Organization",
    "OrganizationMember",
    "Product",
    "ProductIdentifier",
    "ProductSource",
    "ProductSpecification",
    "ProductVariant",
    "SourceDocument",
    "SourceSnapshot",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "WorkspaceMember",
    "WorkspaceProduct",
]
