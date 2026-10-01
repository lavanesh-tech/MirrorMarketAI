"""Audit trail: who did what, to what, from where, and whether it worked.

`record()` only adds the row; it commits with the caller's transaction, so an
audited change and its audit entry succeed or fail together. Details must never
contain secrets (passwords, tokens) or full personal data: emails are hashed.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_client_ip, get_request_id
from app.models.security import AuditLog

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"

LOGIN = "auth.login"
REGISTER = "auth.register"
REFRESH = "auth.refresh"
REFRESH_REUSE = "auth.refresh_reuse_detected"
LOGOUT = "auth.logout"
LOGOUT_ALL = "auth.logout_all"
PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105 - an action name
WORKSPACE_CREATED = "workspace.created"
WORKSPACE_UPDATED = "workspace.updated"
COMMENT_MODERATED = "comment.moderated"
SOURCE_UPLOADED = "source.uploaded"
SOURCE_REJECTED = "source.rejected"


def email_fingerprint(email: str) -> str:
    """Stable, non-reversible reference to an email for correlating failed logins."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def record(
    session: AsyncSession,
    action: str,
    *,
    outcome: str = SUCCESS,
    actor_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            outcome=outcome,
            actor_id=actor_id,
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=get_client_ip(),
            request_id=get_request_id(),
            details=details or {},
        )
    )
