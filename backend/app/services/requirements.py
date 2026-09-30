"""Purchase requirements: extraction, versioned saves with optimistic locking, history."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    RequirementNotFoundError,
    RequirementTextTooLongError,
    RequirementVersionConflictError,
    RequirementVersionNotFoundError,
)
from app.domain.requirements import RequirementDiff, RequirementSpec, diff_specs
from app.domain.roles import WorkspaceRole
from app.models.identity import User
from app.models.requirements import PurchaseRequirement, RequirementVersion
from app.providers.extraction import (
    Extraction,
    ExtractionError,
    RequirementExtractor,
    RuleBasedExtractor,
)
from app.services.workspaces import WorkspaceService

logger = logging.getLogger(__name__)

MANUAL_EXTRACTOR = "manual"


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    extraction: Extraction
    degraded: bool


@dataclass(frozen=True, slots=True)
class SaveOutcome:
    requirement: PurchaseRequirement
    version: RequirementVersion
    created: bool
    degraded: bool


class RequirementService:
    def __init__(
        self, session: AsyncSession, settings: Settings, extractor: RequirementExtractor
    ) -> None:
        self.session = session
        self.settings = settings
        self.extractor = extractor
        self.workspaces = WorkspaceService(session)

    # ------------------------------------------------------------- extraction
    async def extract(self, text: str) -> ExtractOutcome:
        if len(text) > self.settings.requirements_max_text_chars:
            raise RequirementTextTooLongError(
                f"Requirement text is limited to {self.settings.requirements_max_text_chars} "
                "characters."
            )
        try:
            return ExtractOutcome(await self.extractor.extract(text), degraded=False)
        except ExtractionError as exc:
            if isinstance(self.extractor, RuleBasedExtractor):
                raise  # pragma: no cover - the rule extractor never raises
            logger.warning("requirement extraction degraded to rules", extra={"error": str(exc)})
            return ExtractOutcome(await RuleBasedExtractor().extract(text), degraded=True)

    async def preview(self, workspace_id: uuid.UUID, user: User, text: str) -> ExtractOutcome:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.EDITOR)
        return await self.extract(text)

    # ------------------------------------------------------------------ saves
    async def save(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        text: str | None,
        spec: RequirementSpec | None,
        expected_version: int,
        change_note: str | None,
    ) -> SaveOutcome:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.EDITOR)

        degraded = False
        unparsed: list[str] = []
        if spec is not None:
            extractor_name = MANUAL_EXTRACTOR
        else:
            assert text is not None  # noqa: S101 - guaranteed by the request schema
            outcome = await self.extract(text)
            spec, unparsed = outcome.extraction.spec, outcome.extraction.unparsed
            extractor_name, degraded = outcome.extraction.extractor, outcome.degraded

        requirement = await self._lock_or_create(workspace_id, user)
        if requirement.current_version != expected_version:
            raise RequirementVersionConflictError(
                f"Current version is {requirement.current_version}, "
                f"but expected_version was {expected_version}."
            )

        if requirement.current_version > 0:
            latest = await self._version(requirement.id, requirement.current_version)
            if latest.spec == spec.to_json() and latest.raw_text == text:
                # Saving identical content is a no-op, so retries and double-clicks are safe.
                return SaveOutcome(requirement, latest, created=False, degraded=degraded)

        requirement.current_version += 1
        version = RequirementVersion(
            requirement_id=requirement.id,
            version=requirement.current_version,
            raw_text=text,
            spec=spec.to_json(),
            unparsed=unparsed,
            extractor=extractor_name,
            change_note=change_note,
            created_by_id=user.id,
        )
        self.session.add(version)
        await self.session.flush()
        return SaveOutcome(requirement, version, created=True, degraded=degraded)

    async def _lock_or_create(self, workspace_id: uuid.UUID, user: User) -> PurchaseRequirement:
        """Row-lock the workspace's requirement (FOR UPDATE), creating it on first save."""
        stmt = (
            select(PurchaseRequirement)
            .where(PurchaseRequirement.workspace_id == workspace_id)
            .with_for_update()
        )
        requirement = await self.session.scalar(stmt)
        if requirement is not None:
            return requirement
        requirement = PurchaseRequirement(
            workspace_id=workspace_id, created_by_id=user.id, current_version=0
        )
        try:
            async with self.session.begin_nested():
                self.session.add(requirement)
                await self.session.flush()
        except IntegrityError as exc:
            # A concurrent first save won the race; the caller's expected_version is stale.
            raise RequirementVersionConflictError from exc
        return requirement

    # ------------------------------------------------------------------ reads
    async def _requirement(self, workspace_id: uuid.UUID, user: User) -> PurchaseRequirement:
        await self.workspaces.authorize(workspace_id, user)
        requirement = await self.session.scalar(
            select(PurchaseRequirement).where(PurchaseRequirement.workspace_id == workspace_id)
        )
        if requirement is None or requirement.current_version == 0:
            raise RequirementNotFoundError
        return requirement

    async def _version(self, requirement_id: uuid.UUID, number: int) -> RequirementVersion:
        version = await self.session.scalar(
            select(RequirementVersion).where(
                RequirementVersion.requirement_id == requirement_id,
                RequirementVersion.version == number,
            )
        )
        if version is None:
            raise RequirementVersionNotFoundError
        return version

    async def current(
        self, workspace_id: uuid.UUID, user: User
    ) -> tuple[PurchaseRequirement, RequirementVersion]:
        requirement = await self._requirement(workspace_id, user)
        return requirement, await self._version(requirement.id, requirement.current_version)

    async def get_version(
        self, workspace_id: uuid.UUID, user: User, number: int
    ) -> RequirementVersion:
        requirement = await self._requirement(workspace_id, user)
        return await self._version(requirement.id, number)

    async def list_versions(
        self, workspace_id: uuid.UUID, user: User, *, limit: int, offset: int
    ) -> tuple[list[RequirementVersion], int]:
        requirement = await self._requirement(workspace_id, user)
        where = RequirementVersion.requirement_id == requirement.id
        total = await self.session.scalar(
            select(func.count()).select_from(RequirementVersion).where(where)
        )
        rows = await self.session.scalars(
            select(RequirementVersion)
            .where(where)
            .order_by(RequirementVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def diff(
        self, workspace_id: uuid.UUID, user: User, from_version: int, to_version: int
    ) -> RequirementDiff:
        requirement = await self._requirement(workspace_id, user)
        before = await self._version(requirement.id, from_version)
        after = await self._version(requirement.id, to_version)
        return diff_specs(
            RequirementSpec.model_validate(before.spec), RequirementSpec.model_validate(after.spec)
        )
