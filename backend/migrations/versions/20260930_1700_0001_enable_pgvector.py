"""Baseline: enable the pgvector extension.

Revision ID: 0001
Revises:
Create Date: 2026-09-30 17:00:00+00:00

The local Docker init script also enables pgvector, but managed databases
(AWS RDS) never run that script. This migration is what guarantees the extension
exists everywhere the schema is deployed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Safe only because no table uses the `vector` type yet. Once embedding
    # tables exist (Phase 6), their own migrations must be downgraded first.
    op.execute("DROP EXTENSION IF EXISTS vector")
