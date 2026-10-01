"""Application errors and the API-wide error envelope.

Every error response has the same shape:

    {"error": {"code": "workspace_not_found", "message": "...", "request_id": "..."}}

Services raise `AppError` subclasses (never HTTPException), so business logic
stays independent of HTTP; the handlers here translate them to responses.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import get_request_id


class AppError(Exception):
    status_code: ClassVar[int] = status.HTTP_400_BAD_REQUEST
    code: ClassVar[str] = "bad_request"
    message: ClassVar[str] = "The request could not be processed."
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(self, message: str | None = None) -> None:
        self.detail = message or self.message
        self.extra_headers: dict[str, str] = {}
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    message = "Authentication is required."
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    message = "Incorrect email or password."


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class WorkspaceNotFoundError(NotFoundError):
    code = "workspace_not_found"
    message = "Workspace not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state."


class EmailAlreadyRegisteredError(ConflictError):
    code = "email_already_registered"
    message = "An account with this email already exists."


class ProductNotFoundError(NotFoundError):
    code = "product_not_found"
    message = "Product not found."


class VariantNotFoundError(NotFoundError):
    code = "variant_not_found"
    message = "Variant not found for this product."


class WorkspaceProductNotFoundError(NotFoundError):
    code = "workspace_product_not_found"
    message = "This product is not in the workspace."


class ProductAlreadyExistsError(ConflictError):
    code = "product_already_exists"
    message = "A product with this brand and name already exists."


class VariantAlreadyExistsError(ConflictError):
    code = "variant_already_exists"
    message = "This product already has a variant with that name."


class IdentifierAlreadyExistsError(ConflictError):
    code = "identifier_already_exists"
    message = "This identifier is already assigned to a product."


class ProductAlreadyInWorkspaceError(ConflictError):
    code = "product_already_in_workspace"
    message = "This product is already in the workspace."


class UnprocessableError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unprocessable"
    message = "The request is well-formed but semantically invalid."


class InvalidIdentifierValueError(UnprocessableError):
    code = "invalid_identifier"


class SourceNotFoundError(NotFoundError):
    code = "source_not_found"
    message = "Source not found."


class SourceHasNoUrlError(ConflictError):
    code = "source_has_no_url"
    message = "This source has no URL to fetch."


class UnsafeUrlError(UnprocessableError):
    code = "unsafe_url"
    message = "This URL is not allowed."


class SourceUnparseableError(UnprocessableError):
    code = "unparseable_source"
    message = "No usable text could be extracted from this source."


class SourceFetchFailedError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "source_fetch_failed"
    message = "The source could not be fetched."


class FileTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "file_too_large"
    message = "The uploaded file is too large."


class EmbeddingJobNotFoundError(NotFoundError):
    code = "embedding_job_not_found"
    message = "Embedding job not found."


class RequirementNotFoundError(NotFoundError):
    code = "requirement_not_found"
    message = "This workspace has no purchase requirements yet."


class RequirementVersionNotFoundError(NotFoundError):
    code = "requirement_version_not_found"
    message = "Requirement version not found."


class RequirementVersionConflictError(ConflictError):
    code = "requirement_version_conflict"
    message = "Requirements were changed by someone else. Reload and try again."


class RequirementTextTooLongError(UnprocessableError):
    code = "requirement_text_too_long"
    message = "The requirement text is too long."


class EvidencePackNotFoundError(NotFoundError):
    code = "evidence_pack_not_found"
    message = "Evidence pack not found."


class NoEvidenceFoundError(UnprocessableError):
    code = "no_evidence_found"
    message = "No evidence in this workspace matches the query."


class AgentRunNotFoundError(NotFoundError):
    code = "agent_run_not_found"
    message = "Agent run not found."


class NoCompatibilityTargetsError(UnprocessableError):
    code = "no_compatibility_targets"
    message = (
        "Nothing to check: add owned devices to the requirements or the request "
        "(e.g. 'iPhone 15', 'USB-C dock')."
    )


class NothingToCompareError(UnprocessableError):
    code = "nothing_to_compare"
    message = "Add products to the workspace and save requirements (or a budget) to compare."


class SearchUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "search_unavailable"
    message = "Semantic search is temporarily unavailable. Try mode 'lexical'."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Retry after the number of seconds in Retry-After."

    def __init__(self, retry_after_seconds: int, limit: int) -> None:
        super().__init__()
        self.extra_headers = {
            "Retry-After": str(max(1, retry_after_seconds)),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        }


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": get_request_id(), **extra}}


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - registered only for AppError
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.detail),
        headers={**(exc.headers or {}), **exc.extra_headers} or None,
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Report where and why, but never echo the submitted values back
    # (they may contain passwords).
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body("validation_error", "The request is invalid.", details=details),
    )


_HTTP_CODES = {
    404: ("not_found", "The requested resource was not found."),
    405: ("method_not_allowed", "This method is not allowed for this resource."),
}


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code, message = _HTTP_CODES.get(exc.status_code, ("http_error", str(exc.detail)))
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message),
        headers=getattr(exc, "headers", None),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
