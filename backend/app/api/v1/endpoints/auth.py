"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, RateLimiterDep, SessionDep, SettingsDep
from app.api.rate_limits import auth_rate_limit, enforce
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.auth import (
    AuditLogItem,
    AuditLogList,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.audit import AuditService
from app.services.auth import AuthService, Session

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(session: Session) -> TokenResponse:
    return TokenResponse(
        access_token=session.access.token,
        expires_at=session.access.expires_at,
        refresh_token=session.refresh_token,
        refresh_expires_at=session.refresh_expires_at,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={
        409: {"description": "Email already registered"},
        429: {"description": "Rate limited"},
    },
    dependencies=[Depends(auth_rate_limit)],
)
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> UserResponse:
    user = await AuthService(session, settings).register(
        email=body.email, password=body.password, display_name=body.display_name
    )
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email + password for an access token",
    responses={
        401: {"description": "Incorrect email or password"},
        429: {"description": "Rate limited"},
    },
    dependencies=[Depends(auth_rate_limit)],
)
async def login(
    body: LoginRequest, session: SessionDep, settings: SettingsDep, limiter: RateLimiterDep
) -> TokenResponse:
    if settings.rate_limit_enabled:
        # Per account too, so a botnet cannot spread guesses for one email across IPs.
        email = body.email.strip().lower()
        await enforce(limiter, None, "auth:login-email", email, settings.rate_limit_auth_per_minute)
    return _tokens(
        await AuthService(session, settings).login(email=body.email, password=body.password)
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access + refresh token (rotation)",
    description="The presented refresh token becomes invalid. Presenting it again revokes "
    "the whole session (stolen-token detection).",
    responses={401: {"description": "Invalid, expired, revoked or reused refresh token"}},
    dependencies=[Depends(auth_rate_limit)],
)
async def refresh(
    body: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    return _tokens(await AuthService(session, settings).refresh(body.refresh_token))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End this session (revokes its refresh tokens)",
)
async def logout(body: RefreshRequest, session: SessionDep, settings: SettingsDep) -> None:
    await AuthService(session, settings).logout(body.refresh_token)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session on every device (access tokens stop working too)",
)
async def logout_all(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> None:
    await AuthService(session, settings).logout_all(user)


@router.post(
    "/change-password",
    response_model=TokenResponse,
    summary="Change the password; all other sessions are ended and a new one is returned",
    responses={401: {"description": "Current password incorrect"}},
    dependencies=[Depends(auth_rate_limit)],
)
async def change_password(
    body: ChangePasswordRequest, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    return _tokens(
        await AuthService(session, settings).change_password(
            user, current=body.current_password, new=body.new_password
        )
    )


@router.get(
    "/audit-logs", response_model=AuditLogList, summary="Your own security events, newest first"
)
async def my_audit_logs(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogList:
    rows, total = await AuditService(session).for_user(user, limit=limit, offset=offset)
    return AuditLogList(
        items=[AuditLogItem.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
