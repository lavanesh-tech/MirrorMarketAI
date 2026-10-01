"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, RateLimiterDep, SessionDep, SettingsDep
from app.api.rate_limits import auth_rate_limit, enforce
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


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
    token = await AuthService(session, settings).login(email=body.email, password=body.password)
    return TokenResponse(access_token=token.token, expires_at=token.expires_at)


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
