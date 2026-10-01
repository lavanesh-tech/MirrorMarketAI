"""Agent execution and run history."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, EmbedderDep, LLMDep, SessionDep, SettingsDep
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.agents import AgentRunListResponse, AgentRunResponse, CompatibilityRequest
from app.schemas.workspaces import PageMeta
from app.services.agents import AgentService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["agents"])


@router.post(
    "/products/{product_id}/research",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Product Research Agent for a workspace product (cited facts)",
)
async def research_product(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).research_product(
        workspace_id, user, product_id
    )
    return AgentRunResponse.model_validate(run)


@router.post(
    "/products/{product_id}/reviews/analyze",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Review Intelligence Agent (aspect sentiment over REVIEW sources)",
)
async def analyze_reviews(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).analyze_reviews(
        workspace_id, user, product_id
    )
    return AgentRunResponse.model_validate(run)


@router.post(
    "/products/{product_id}/compatibility",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Compatibility Agent against owned devices (request or requirements)",
)
async def check_compatibility(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
    body: CompatibilityRequest | None = None,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).check_compatibility(
        workspace_id, user, product_id, body.owned_devices if body else None
    )
    return AgentRunResponse.model_validate(run)


@router.post(
    "/products/{product_id}/value",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Value Agent: price, budget fit, requirement fit, value index",
)
async def assess_value(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).assess_value(
        workspace_id, user, product_id
    )
    return AgentRunResponse.model_validate(run)


@router.post(
    "/products/{product_id}/risk",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the Risk Agent: warranty, returns, safety, reliability + other agents' flags",
)
async def assess_risk(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).assess_risk(
        workspace_id, user, product_id
    )
    return AgentRunResponse.model_validate(run)


@router.get("/agent-runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
    agent: Annotated[str | None, Query(max_length=40)] = None,
    product_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentRunListResponse:
    runs, total = await AgentService(session, settings, embedder, llm).list_runs(
        workspace_id, user, agent=agent, product_id=product_id, limit=limit, offset=offset
    )
    return AgentRunListResponse(
        items=[AgentRunResponse.model_validate(r) for r in runs],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
    llm: LLMDep,
) -> AgentRunResponse:
    run = await AgentService(session, settings, embedder, llm).get_run(workspace_id, user, run_id)
    return AgentRunResponse.model_validate(run)
