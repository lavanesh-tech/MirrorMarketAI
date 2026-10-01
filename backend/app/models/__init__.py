"""SQLAlchemy ORM models (PostgreSQL tables).

Import every model module here so `Base.metadata` is complete for Alembic
autogenerate and the schema-drift test.
"""

from app.models.agents import AgentRun
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.catalog import (
    Product,
    ProductIdentifier,
    ProductSpecification,
    ProductVariant,
    WorkspaceProduct,
)
from app.models.collaboration import ProductVote, WorkspaceComment
from app.models.events import OutboxEvent, ProcessedEvent, WorkspaceActivity
from app.models.evidence import EvidenceItem, EvidencePack
from app.models.identity import (
    ComparisonWorkspace,
    Organization,
    OrganizationMember,
    User,
    WorkspaceMember,
)
from app.models.prices import PriceSnapshot
from app.models.requirements import PurchaseRequirement, RequirementVersion
from app.models.retrieval import ChunkEmbedding, DocumentChunk, EmbeddingJob
from app.models.security import AuditLog, RefreshToken
from app.models.sources import ProductSource, SourceDocument, SourceSnapshot

__all__ = [
    "AgentRun",
    "AuditLog",
    "Base",
    "ChunkEmbedding",
    "ComparisonWorkspace",
    "DocumentChunk",
    "EmbeddingJob",
    "EvidenceItem",
    "EvidencePack",
    "Organization",
    "OrganizationMember",
    "OutboxEvent",
    "PriceSnapshot",
    "ProcessedEvent",
    "Product",
    "ProductIdentifier",
    "ProductSource",
    "ProductSpecification",
    "ProductVariant",
    "ProductVote",
    "PurchaseRequirement",
    "RefreshToken",
    "RequirementVersion",
    "SourceDocument",
    "SourceSnapshot",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "WorkspaceActivity",
    "WorkspaceComment",
    "WorkspaceMember",
    "WorkspaceProduct",
]
