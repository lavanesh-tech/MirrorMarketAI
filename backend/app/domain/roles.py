"""Workspace roles and the permission rules built on them.

This is deliberately pure Python (no I/O): authorization rules are the kind of
thing that must be deterministic and exhaustively unit-tested.
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "OWNER"  # everything, including deleting the workspace and managing owners
    EDITOR = "EDITOR"  # edit workspace details, requirements, products
    MEMBER = "MEMBER"  # participate: comment, vote, ask questions
    VIEWER = "VIEWER"  # read-only


_RANK = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.MEMBER: 1,
    WorkspaceRole.EDITOR: 2,
    WorkspaceRole.OWNER: 3,
}


def has_at_least(role: WorkspaceRole, minimum: WorkspaceRole) -> bool:
    return _RANK[role] >= _RANK[minimum]


class OrganizationRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
