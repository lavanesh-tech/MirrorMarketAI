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

__all__ = [
    "Base",
    "ComparisonWorkspace",
    "Organization",
    "OrganizationMember",
    "Product",
    "ProductIdentifier",
    "ProductSpecification",
    "ProductVariant",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "WorkspaceMember",
    "WorkspaceProduct",
]
