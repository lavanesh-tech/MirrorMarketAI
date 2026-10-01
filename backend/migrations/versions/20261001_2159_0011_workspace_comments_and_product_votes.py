"""Workspace comments (soft-deleted, one-level threads) and product votes.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01 21:59:07.838769+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_votes",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("value IN (-1, 1)", name=op.f("ck_product_votes_value_valid")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_votes_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_product_votes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["comparison_workspaces.id"],
            name=op.f("fk_product_votes_workspace_id_comparison_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_votes")),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "user_id",
            name=op.f("uq_product_votes_workspace_id_product_id_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_product_votes_product_id"), "product_votes", ["product_id"], unique=False
    )
    op.create_index(op.f("ix_product_votes_user_id"), "product_votes", ["user_id"], unique=False)
    op.create_table(
        "workspace_comments",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 4000", name=op.f("ck_workspace_comments_body_length")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_workspace_comments_author_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["workspace_comments.id"],
            name=op.f("fk_workspace_comments_parent_id_workspace_comments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_workspace_comments_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["comparison_workspaces.id"],
            name=op.f("fk_workspace_comments_workspace_id_comparison_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_comments")),
    )
    op.create_index(
        op.f("ix_workspace_comments_author_id"), "workspace_comments", ["author_id"], unique=False
    )
    op.create_index(
        op.f("ix_workspace_comments_parent_id"), "workspace_comments", ["parent_id"], unique=False
    )
    op.create_index(
        op.f("ix_workspace_comments_product_id"), "workspace_comments", ["product_id"], unique=False
    )
    op.create_index(
        "ix_workspace_comments_thread",
        "workspace_comments",
        ["workspace_id", "product_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_comments_thread", table_name="workspace_comments")
    op.drop_index(op.f("ix_workspace_comments_product_id"), table_name="workspace_comments")
    op.drop_index(op.f("ix_workspace_comments_parent_id"), table_name="workspace_comments")
    op.drop_index(op.f("ix_workspace_comments_author_id"), table_name="workspace_comments")
    op.drop_table("workspace_comments")
    op.drop_index(op.f("ix_product_votes_user_id"), table_name="product_votes")
    op.drop_index(op.f("ix_product_votes_product_id"), table_name="product_votes")
    op.drop_table("product_votes")
