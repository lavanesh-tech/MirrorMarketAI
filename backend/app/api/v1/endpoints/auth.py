"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={409: {"description": "Email already registered"}},
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
    responses={401: {"description": "Incorrect email or password"}},
)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    token = await AuthService(session, settings).login(email=body.email, password=body.password)
    return TokenResponse(access_token=token.token, expires_at=token.expires_at)


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
