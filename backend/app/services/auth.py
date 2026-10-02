"""Registration, login and session lifecycle (refresh, logout, password change).

Sessions: login returns a short-lived access token (JWT) and an opaque refresh
token. Each refresh ROTATES the token: the old one is marked used and a new one
is issued in the same "family". Presenting a token that was already rotated can
only mean it was stolen (or replayed), so the whole family is revoked and the
user must log in again. The one exception is a short leeway right after a rotation,
so two tabs refreshing at the same moment do not log the user out. Rotation never
extends a family past its absolute expiry.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.domain.roles import OrganizationRole
from app.models.identity import Organization, OrganizationMember, User
from app.models.security import RefreshToken
from app.repositories.identity import OrganizationRepository, UserRepository
from app.security import audit_trail as audit
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.refresh import hash_refresh_token, new_refresh_token
from app.security.tokens import AccessToken, create_access_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Session:
    access: AccessToken
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.organizations = OrganizationRepository(session)

    async def register(self, *, email: str, password: str, display_name: str) -> User:
        """Create the user and their personal organization in one transaction."""
        email = email.strip().lower()
        if await self.users.get_by_email(email) is not None:
            raise EmailAlreadyRegisteredError
        try:
            user = await self.users.add(
                User(email=email, password_hash=hash_password(password), display_name=display_name)
            )
            organization = await self.organizations.add(
                Organization(
                    name=f"{display_name}'s space", is_personal=True, created_by_id=user.id
                )
            )
            await self.organizations.add_member(
                OrganizationMember(
                    organization_id=organization.id, user_id=user.id, role=OrganizationRole.OWNER
                )
            )
            audit.record(self.session, audit.REGISTER, actor_id=user.id)
            await self.session.commit()
        except IntegrityError as exc:
            # Two concurrent registrations with the same email: the unique index wins.
            await self.session.rollback()
            raise EmailAlreadyRegisteredError from exc
        logger.info("user registered", extra={"user_id": str(user.id)})
        return user

    # ------------------------------------------------------------------ sessions
    def _new_refresh_row(
        self, user_id: uuid.UUID, family_id: uuid.UUID, family_expires_at: datetime
    ) -> tuple[RefreshToken, str]:
        token = new_refresh_token()
        now = datetime.now(UTC)
        expires = min(now + timedelta(days=self.settings.refresh_token_ttl_days), family_expires_at)
        row = RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_refresh_token(token),
            expires_at=expires,
            family_expires_at=family_expires_at,
        )
        self.session.add(row)
        return row, token

    def _start_session(self, user: User) -> Session:
        family_expires = datetime.now(UTC) + timedelta(days=self.settings.refresh_session_max_days)
        row, token = self._new_refresh_row(user.id, uuid.uuid4(), family_expires)
        return Session(
            create_access_token(user.id, self.settings, version=user.token_version),
            token,
            row.expires_at,
        )

    async def _revoke(self, *where: object) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.revoked_at.is_(None), *where)  # type: ignore[arg-type]
            .values(revoked_at=datetime.now(UTC))
        )

    async def login(self, *, email: str, password: str) -> Session:
        user = await self.users.get_by_email(email.strip())
        # verify_password always hashes (dummy hash for unknown users) so
        # response time doesn't reveal which emails are registered.
        valid = verify_password(password, user.password_hash if user else None)
        if user is None or not valid or not user.is_active:
            logger.info("login failed")
            audit.record(
                self.session,
                audit.LOGIN,
                outcome=audit.FAILURE,
                actor_id=user.id if user else None,
                details={"email_fingerprint": audit.email_fingerprint(email)},
            )
            await self.session.commit()
            raise InvalidCredentialsError
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        session = self._start_session(user)
        audit.record(self.session, audit.LOGIN, actor_id=user.id)
        await self.session.commit()
        logger.info("login succeeded", extra={"user_id": str(user.id)})
        return session

    async def refresh(self, refresh_token: str) -> Session:
        # FOR UPDATE: two requests racing with the same token are serialised, so
        # exactly one rotates it and the other is treated as a replay.
        row: RefreshToken | None = await self.session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
            .with_for_update()
        )
        now = datetime.now(UTC)
        if row is None:
            raise InvalidRefreshTokenError
        leeway = timedelta(seconds=self.settings.refresh_reuse_leeway_seconds)
        raced = row.rotated_at is not None and now - row.rotated_at <= leeway
        if row.rotated_at is not None and row.revoked_at is None and not raced:
            await self._revoke(RefreshToken.family_id == row.family_id)
            audit.record(
                self.session,
                audit.REFRESH_REUSE,
                outcome=audit.FAILURE,
                actor_id=row.user_id,
                details={"family_id": str(row.family_id)},
            )
            await self.session.commit()
            logger.warning("refresh token reuse detected", extra={"user_id": str(row.user_id)})
            raise InvalidRefreshTokenError
        user = await self.users.get(row.user_id)
        if row.revoked_at is not None or row.expires_at <= now or not (user and user.is_active):
            raise InvalidRefreshTokenError
        if row.rotated_at is None:
            row.rotated_at = now  # a raced re-use keeps the original rotation time
        new_row, token = self._new_refresh_row(user.id, row.family_id, row.family_expires_at)
        audit.record(self.session, audit.REFRESH, actor_id=user.id)
        await self.session.commit()
        return Session(
            create_access_token(user.id, self.settings, version=user.token_version),
            token,
            new_row.expires_at,
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke this session's token family. Unknown tokens are ignored (idempotent)."""
        row: RefreshToken | None = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        )
        if row is None:
            return
        await self._revoke(RefreshToken.family_id == row.family_id)
        audit.record(self.session, audit.LOGOUT, actor_id=row.user_id)
        await self.session.commit()

    async def _invalidate_everything(self, user: User) -> None:
        await self._revoke(RefreshToken.user_id == user.id)
        user.token_version += 1  # every access token issued so far now fails verification

    async def logout_all(self, user: User) -> None:
        """Revoke every refresh token and reject every access token issued so far."""
        await self._invalidate_everything(user)
        audit.record(self.session, audit.LOGOUT_ALL, actor_id=user.id)
        await self.session.commit()

    async def change_password(self, user: User, *, current: str, new: str) -> Session:
        """Verify the current password, set the new one, end all other sessions."""
        if not verify_password(current, user.password_hash):
            audit.record(
                self.session, audit.PASSWORD_CHANGED, outcome=audit.FAILURE, actor_id=user.id
            )
            await self.session.commit()
            raise InvalidCredentialsError("The current password is incorrect.")
        user.password_hash = hash_password(new)
        await self._invalidate_everything(user)
        session = self._start_session(user)
        audit.record(self.session, audit.PASSWORD_CHANGED, actor_id=user.id)
        await self.session.commit()
        return session
