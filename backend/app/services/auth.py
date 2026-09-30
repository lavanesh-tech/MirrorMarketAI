"""Registration and login use-cases."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import EmailAlreadyRegisteredError, InvalidCredentialsError
from app.domain.roles import OrganizationRole
from app.models.identity import Organization, OrganizationMember, User
from app.repositories.identity import OrganizationRepository, UserRepository
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.tokens import AccessToken, create_access_token

logger = logging.getLogger(__name__)


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
            await self.session.commit()
        except IntegrityError as exc:
            # Two concurrent registrations with the same email: the unique index wins.
            await self.session.rollback()
            raise EmailAlreadyRegisteredError from exc
        logger.info("user registered", extra={"user_id": str(user.id)})
        return user

    async def login(self, *, email: str, password: str) -> AccessToken:
        user = await self.users.get_by_email(email.strip())
        # verify_password always hashes (dummy hash for unknown users) so
        # response time doesn't reveal which emails are registered.
        valid = verify_password(password, user.password_hash if user else None)
        if user is None or not valid or not user.is_active:
            logger.info("login failed")
            raise InvalidCredentialsError
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            await self.session.commit()
        logger.info("login succeeded", extra={"user_id": str(user.id)})
        return create_access_token(user.id, self.settings)
