"""Comprehensive `/api/v1` REST + SSE Router for APEX ONE 1.0 (Part VII)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from apex_one.platform.governance import GovernanceBlockedError, ImmutableAuditError
from apex_one.platform.research_engine import InvalidResearchStateTransitionError
from apex_one.platform.scientific_connectors import SSRFBlockedError
from apex_one.platform.workspace_service import (
    ApexPlatformService,
    AuthorizationError,
    ResourceNotFoundError,
)


class RegisterBody(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=160)
    organization_name: str | None = None
    locale: Literal["pl", "en"] = "pl"


class LoginBody(BaseModel):
    email: str
    password: str


class UserPreferencesBody(BaseModel):
    display_name: str | None = None
    locale: Literal["pl", "en"] | None = None
    preferences: dict[str, Any] | None = None


class CreateOrgBody(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class InviteBody(BaseModel):
    email: str
    role_name: str = "Researcher"


class CreateApiKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["read:all"])


class CreateAgentBody(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str | None = None
    description: str = ""
    role: str = "specialist"
    purpose: str = ""
    instructions: str = "Postępuj zgodnie z rygorem dowodowym."
    capabilities: list[str] = Field(default_factory=lambda: ["research_synthesis"])
    allowed_tools: list[str] = Field(default_factory=lambda: ["memory.retrieve"])
    model_policy: dict[str, Any] = Field(default_factory=dict)
    memory_policy: dict[str, Any] = Field(default_factory=dict)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(
        default_factory=lambda: {"max_tokens": 24000, "max_cost_usd": 1.5}
    )
    max_steps: int = Field(default=8, ge=1, le=50)
    timeout: int = Field(default=90, ge=5, le=600)
    evaluation_profile: str = "research_rigor_v1"


class UpdateAgentBody(BaseModel):
    updates: dict[str, Any]
    change_summary: str = "Updated AgentDNA configuration"


class AgentStatusBody(BaseModel):
    status: Literal["active", "inactive", "archived"]


class CreateTeamBody(BaseModel):
    name: str
    description: str = ""
    supervisor_agent_id: str | None = None
    member_agent_ids: list[str] = Field(default_factory=list)


class LaunchRunBody(BaseModel):
    objective: str = Field(min_length=3)
    execution_mode: Literal["single_agent", "multi_agent_team"] = "multi_agent_team"
    research_project_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    interrupt_after_step: int | None = None
    model_identifier: str = "apex-analytical-local-v1"
    async_execution: bool = False
    auto_discover_sources: bool = False


class FetchUrlSourceBody(BaseModel):
    url: str = Field(min_length=8)
    research_project_id: str | None = None
    title_override: str | None = None


class DiscoverSourcesBody(BaseModel):
    query: str = Field(min_length=2)
    connectors: list[str] = Field(
        default_factory=lambda: ["openalex", "arxiv", "crossref", "github"]
    )
    max_per_connector: int = Field(default=5, ge=1, le=20)
    auto_register_project_id: str | None = None


class ConfigureProviderSecretBody(BaseModel):
    api_key: str = ""
    base_url: str | None = None


class TestProviderInferenceBody(BaseModel):
    model_identifier: str = "gpt-4.1-mini"
    prompt: str = "Summarize empirical verification principles in two sentences."


class CreateProjectBody(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    hypothesis: str = ""
    scope_summary: str = ""


class TransitionStateBody(BaseModel):
    target_state: str


class UpsertBriefBody(BaseModel):
    objective: str
    scope_summary: str = ""
    methodology: str = ""
    expected_deliverables: str = ""
    questions: list[str] | None = None


class RegisterSourceBody(BaseModel):
    source_type: Literal["URL", "PDF", "DOCX", "TXT", "MD"]
    title: str = Field(min_length=2)
    uri_or_filename: str = Field(min_length=1)
    raw_text: str | None = None
    content_base64: str | None = None
    research_project_id: str | None = None
    authors: list[str] = Field(default_factory=list)
    published_at: str | None = None
    copyright_notice: str = "Source attribution preserved."


class EvidenceLinkInput(BaseModel):
    source_id: str
    source_location: str = "section:1"
    supporting_text: str
    evidence_type: Literal["supporting", "contradicting", "contextual"] = "supporting"
    relevance: float = Field(default=0.85, ge=0.0, le=1.0)


class CreateClaimBody(BaseModel):
    research_project_id: str
    statement: str = Field(min_length=5)
    claim_type: str = "empirical"
    evidence_links: list[EvidenceLinkInput] = Field(default_factory=list)


class PatchClaimStatusBody(BaseModel):
    status: Literal[
        "supported",
        "partially_supported",
        "contradicted",
        "unresolved",
        "rejected",
    ]


class CreateMemoryBody(BaseModel):
    memory_type: str
    title: str = Field(min_length=2)
    content: str = Field(min_length=2)
    source_ref: str = "manual://workbench"
    project_id: str | None = None
    retention_days: int = Field(default=365, ge=1, le=3650)


class MemoryActionBody(BaseModel):
    action: Literal["reindex", "delete"]


class CreateDatasetBody(BaseModel):
    name: str = Field(min_length=2)
    description: str = ""
    category: str = "empirical"
    records: list[dict[str, Any]]


class CreateExperimentBody(BaseModel):
    name: str = Field(min_length=2)
    description: str = ""
    dataset_id: str
    dataset_version: int = 1
    execution_mode: Literal["single_agent", "multi_agent_team"] = "multi_agent_team"
    agent_id: str | None = None
    team_id: str | None = None
    model_identifier: str = "apex-analytical-local-v1"
    parameters: dict[str, Any] = Field(default_factory=dict)
    research_project_id: str | None = None


class RunBenchmarkBody(BaseModel):
    suite_id: str
    label: str = Field(min_length=2)
    execution_mode: Literal["single_agent", "multi_agent_team"] = "multi_agent_team"
    agent_id: str | None = None
    team_id: str | None = None
    model_identifier: str = "apex-analytical-local-v1"
    baseline_eval_run_id: str | None = None


class HumanEvalBody(BaseModel):
    category: str
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=3)


class RegisterModelProviderBody(BaseModel):
    slug: str
    name: str
    category: str = "compatible_http"
    base_url: str = ""
    auth_env_var: str | None = None
    fallback_provider_slug: str | None = "apex-analytical"
    rate_limit_rpm: int = 60
    budget_limit_usd: float = 50.0
    models: list[dict[str, Any]] = Field(default_factory=list)


class RegisterDeviceBody(BaseModel):
    name: str = Field(min_length=2)
    allowed_directories: list[str] = Field(default_factory=list)
    can_read: bool = True
    can_write: bool = False
    can_execute: bool = False


class CreateAutomationWorkflowBody(BaseModel):
    name: str = Field(min_length=2)
    description: str = ""
    risk_level: Literal["R0", "R1", "R2", "R3"] = "R1"
    require_approval: bool = False


class DecideApprovalBody(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    decision_note: str = ""


class EmergencyStopBody(BaseModel):
    active: bool
    reason: str = ""


class ToggleExtensionBody(BaseModel):
    enabled: bool


def create_platform_router(get_platform: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["APEX ONE v1"])

    def require_auth_context(
        request: Request,
        authorization: str | None = Header(default=None),
        x_apex_session_token: str | None = Header(default=None),
        x_organization_id: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
        apx_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        svc: ApexPlatformService = get_platform()
        token: str | None = None
        used_cookie = False

        apex_header_token = (x_apex_session_token or "").strip()
        bearer_token = (
            authorization[7:].strip()
            if authorization and authorization.lower().startswith("bearer ")
            else ""
        )

        if apex_header_token:
            token = apex_header_token
        elif bearer_token and bearer_token.startswith(("apxsess_", "apxkey_")):
            token = bearer_token
        elif apx_session:
            token = apx_session
            used_cookie = True
        elif bearer_token:
            token = bearer_token

        try:
            ctx = svc.authenticate_token(token)
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": str(exc)},
            ) from exc

        if used_cookie and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected_csrf = ctx.get("csrf_token")
            if expected_csrf and x_csrf_token != expected_csrf:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "CSRF_VALIDATION_FAILED",
                        "message": "Missing or invalid X-CSRF-Token.",
                    },
                )

        org_id = x_organization_id or ctx["default_organization_id"]
        try:
            authz = svc.authorize(
                user_id=ctx["user"]["id"],
                organization_id=org_id,
                required_permission="read:all",
            )
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "FORBIDDEN_TENANT_OR_ROLE", "message": str(exc)},
            ) from exc

        return {
            "svc": svc,
            "user": ctx["user"],
            "user_id": ctx["user"]["id"],
            "organization_id": org_id,
            "role": authz["role"],
            "raw_token": token,
        }

    def handle_domain_call(fn: Any) -> Any:
        try:
            return fn()
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=403, detail={"code": "FORBIDDEN", "message": str(exc)}
            ) from exc
        except GovernanceBlockedError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "GOVERNANCE_BLOCKED", "message": str(exc)},
            ) from exc
        except ImmutableAuditError as exc:
            raise HTTPException(
                status_code=403, detail={"code": "AUDIT_IMMUTABLE", "message": str(exc)}
            ) from exc
        except ResourceNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)}
            ) from exc
        except InvalidResearchStateTransitionError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_STATE_TRANSITION", "message": str(exc)},
            ) from exc
        except SSRFBlockedError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "SSRF_BLOCKED", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail={"code": "VALIDATION_ERROR", "message": str(exc)}
            ) from exc

    @router.post("/auth/register", status_code=201)
    async def register(body: RegisterBody) -> dict[str, Any]:
        svc: ApexPlatformService = get_platform()
        return handle_domain_call(
            lambda: svc.register_user(
                email=body.email,
                password=body.password,
                display_name=body.display_name,
                organization_name=body.organization_name,
                locale=body.locale,
            )
        )

    @router.post("/auth/login")
    async def login(
        body: LoginBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        svc: ApexPlatformService = get_platform()
        try:
            res = svc.login(email=body.email, password=body.password)
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=401,
                detail={"code": "INVALID_CREDENTIALS", "message": str(exc)},
            ) from exc
        forwarded_proto = (
            request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        )
        is_https = request.url.scheme == "https" or forwarded_proto == "https"
        response.set_cookie(
            key="apx_session",
            value=res["access_token"],
            httponly=True,
            secure=is_https,
            samesite="none" if is_https else "lax",
            max_age=43200,
        )
        return res

    @router.post("/auth/logout")
    async def logout(
        response: Response,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        ctx["svc"].logout(ctx["raw_token"])
        response.delete_cookie("apx_session")
        return {"status": "logged_out"}

    @router.get("/auth/me")
    async def auth_me(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        orgs = ctx["svc"].list_user_organizations(ctx["user_id"])
        return {
            "user": ctx["user"],
            "active_organization_id": ctx["organization_id"],
            "role": ctx["role"],
            "organizations": orgs,
        }

    @router.patch("/auth/preferences")
    async def patch_preferences(
        body: UserPreferencesBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].update_user_preferences(
                user_id=ctx["user_id"],
                display_name=body.display_name,
                locale=body.locale,
                preferences=body.preferences,
            )
        )

    @router.get("/organizations")
    async def list_orgs(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        svc: ApexPlatformService = ctx["svc"]
        orgs = svc.list_user_organizations(ctx["user_id"])
        members = [
            {
                **m,
                "user": svc._public_user(svc.users[m["user_id"]])
                if m["user_id"] in svc.users
                else None,
            }
            for m in svc.memberships
            if m["organization_id"] == ctx["organization_id"]
        ]
        invs = [
            {k: v for k, v in i.items() if k not in {"token_hash", "invite_token_once"}}
            for i in svc.invitations.values()
            if i["organization_id"] == ctx["organization_id"]
        ]
        creds = [
            c
            for c in svc.api_credentials.values()
            if c["organization_id"] == ctx["organization_id"]
        ]
        return {
            "organizations": orgs,
            "members": members,
            "invitations": invs,
            "api_credentials": creds,
        }

    @router.post("/organizations", status_code=201)
    async def create_org(
        body: CreateOrgBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_organization(
                user_id=ctx["user_id"], name=body.name
            )
        )

    @router.post("/organizations/invitations", status_code=201)
    async def invite_member(
        body: InviteBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_invitation(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                email=body.email,
                role_name=body.role_name,
            )
        )

    @router.post("/organizations/api-credentials", status_code=201)
    async def create_api_key(
        body: CreateApiKeyBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_api_credential(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                scopes=body.scopes,
            )
        )

    @router.get("/agents")
    async def list_agents(
        scope: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_agents(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )
        return {"total": len(items), "items": items[offset : offset + limit]}

    @router.post("/agents", status_code=201)
    async def create_agent(
        body: CreateAgentBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_agent(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                payload=body.model_dump(),
            )
        )

    @router.get("/agents/{agent_id}")
    async def get_agent(
        agent_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].get_agent_detail(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                agent_id=agent_id,
            )
        )

    @router.put("/agents/{agent_id}")
    async def update_agent(
        agent_id: str,
        body: UpdateAgentBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].update_agent_creates_new_version(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                agent_id=agent_id,
                updates=body.updates,
                change_summary=body.change_summary,
            )
        )

    @router.post("/agents/{agent_id}/duplicate", status_code=201)
    async def duplicate_agent(
        agent_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].duplicate_agent(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                agent_id=agent_id,
            )
        )

    @router.patch("/agents/{agent_id}/status")
    async def patch_agent_status(
        agent_id: str,
        body: AgentStatusBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].set_agent_status(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                agent_id=agent_id,
                status=body.status,
            )
        )

    @router.get("/agents/{agent_id}/compare")
    async def compare_agent_versions(
        agent_id: str,
        version_a: int = Query(..., ge=1),
        version_b: int = Query(..., ge=1),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].compare_agent_versions(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                agent_id=agent_id,
                version_a=version_a,
                version_b=version_b,
            )
        )

    @router.get("/teams")
    async def list_teams(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_teams(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )
        return {"items": items}

    @router.post("/teams", status_code=201)
    async def create_team(
        body: CreateTeamBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_team(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                description=body.description,
                supervisor_agent_id=body.supervisor_agent_id,
                member_agent_ids=body.member_agent_ids,
            )
        )

    @router.get("/runs")
    async def list_runs(
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_agent_runs(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )
        return {"total": len(items), "items": items}

    @router.post("/runs", status_code=201)
    async def launch_run(
        request: Request,
        body: LaunchRunBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        container = request.app.state.container
        if body.async_execution:
            queued = handle_domain_call(
                lambda: ctx["svc"].enqueue_async_run(
                    user_id=ctx["user_id"],
                    organization_id=ctx["organization_id"],
                    objective=body.objective,
                    execution_mode=body.execution_mode,
                    research_project_id=body.research_project_id,
                    agent_id=body.agent_id,
                    team_id=body.team_id,
                    model_identifier=body.model_identifier,
                    auto_discover_sources=body.auto_discover_sources,
                )
            )
            container.async_runner.schedule_run(
                svc=ctx["svc"],
                persistence=container.platform_persistence
                if container.platform_persistence.active
                else None,
                organization_id=ctx["organization_id"],
                user_id=ctx["user_id"],
                run_id=queued["id"],
            )
            return queued

        return await asyncio.to_thread(
            lambda: handle_domain_call(
                lambda: ctx["svc"].launch_agent_run(
                    user_id=ctx["user_id"],
                    organization_id=ctx["organization_id"],
                    objective=body.objective,
                    execution_mode=body.execution_mode,
                    research_project_id=body.research_project_id,
                    agent_id=body.agent_id,
                    team_id=body.team_id,
                    interrupt_after_step=body.interrupt_after_step,
                    model_identifier=body.model_identifier,
                    auto_discover_sources=body.auto_discover_sources,
                )
            )
        )

    @router.post("/runs/{run_id}/resume")
    async def resume_run(
        run_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].resume_interrupted_agent_run(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                run_id=run_id,
            )
        )

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].cancel_agent_run(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                run_id=run_id,
            )
        )

    @router.get("/runs/{run_id}/stream")
    async def stream_run_progress(
        request: Request,
        run_id: str,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> StreamingResponse:
        svc: ApexPlatformService = ctx["svc"]
        container = request.app.state.container
        handle_domain_call(
            lambda: svc._require_org_entity(
                svc.agent_runs, run_id, ctx["organization_id"], "AgentRun"
            )
        )

        async def event_gen() -> Any:
            async for ev in container.async_runner.stream_events(
                svc=svc, run_id=run_id
            ):
                kind = ev.get("event", "step.completed")
                if kind == "step.completed":
                    payload = json.dumps(
                        {
                            "type": "step",
                            "run_id": run_id,
                            "step_index": ev["step_index"],
                            "agent_name": ev["agent"],
                            "role": ev.get("role", ""),
                            "task": ev.get("task", ""),
                            "status": ev["status"],
                            "tokens_used": ev["tokens_used"],
                            "live_llm_invoked": ev.get("live_llm_invoked", False),
                        }
                    )
                    yield f"event: step\ndata: {payload}\n\n"
                elif kind in {"run.completed", "run.cancelled", "run.failed"}:
                    latest = svc.agent_runs.get(run_id, {})
                    done = json.dumps(
                        {
                            "type": "complete",
                            "run_id": run_id,
                            "status": ev.get("status", latest.get("status", "COMPLETED")),
                            "progress_pct": latest.get("progress_pct", 100),
                            "tokens_used": ev.get("tokens_used", latest.get("tokens_used", 0)),
                            "cost_usd": ev.get("cost_usd", latest.get("cost_usd", 0.0)),
                        }
                    )
                    yield f"event: complete\ndata: {done}\n\n"

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/research/projects")
    async def list_projects(
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_research_projects(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )
        return {"total": len(items), "items": items}

    @router.post("/research/projects", status_code=201)
    async def create_project(
        body: CreateProjectBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_research_project(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                title=body.title,
                hypothesis=body.hypothesis,
                scope_summary=body.scope_summary,
            )
        )

    @router.get("/research/projects/{project_id}")
    async def get_project(
        project_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].get_research_project_detail(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
            )
        )

    @router.post("/research/projects/{project_id}/transition")
    async def transition_project(
        project_id: str,
        body: TransitionStateBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].transition_research_project_state(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
                target_state=body.target_state,
            )
        )

    @router.post("/research/projects/{project_id}/brief")
    async def upsert_brief(
        project_id: str,
        body: UpsertBriefBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].upsert_research_brief_and_questions(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
                objective=body.objective,
                scope_summary=body.scope_summary,
                methodology=body.methodology,
                expected_deliverables=body.expected_deliverables,
                questions=body.questions,
            )
        )

    @router.get("/research/projects/{project_id}/export", response_class=PlainTextResponse)
    async def export_project_report(
        project_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> str:
        detail = handle_domain_call(
            lambda: ctx["svc"].get_research_project_detail(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
            )
        )
        reports = detail.get("reports", [])
        if reports:
            return str(reports[-1]["markdown_body"])
        return f"# {detail['title']}\n\nStan: {detail['state']}\n\n{detail.get('scope_summary', '')}\n"

    @router.get("/sources")
    async def list_sources(
        project_id: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_sources(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
                scope=scope,
            )
        )
        return {"total": len(items), "items": items}

    @router.post("/sources", status_code=201)
    async def register_source(
        body: RegisterSourceBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].register_source(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                source_type=body.source_type,
                title=body.title,
                uri_or_filename=body.uri_or_filename,
                raw_text=body.raw_text,
                content_base64=body.content_base64,
                research_project_id=body.research_project_id,
                authors=body.authors,
                published_at=body.published_at,
                copyright_notice=body.copyright_notice,
            )
        )

    @router.post("/sources/fetch-url", status_code=201)
    async def fetch_url_source(
        body: FetchUrlSourceBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            lambda: handle_domain_call(
                lambda: ctx["svc"].fetch_and_register_url_source(
                    user_id=ctx["user_id"],
                    organization_id=ctx["organization_id"],
                    url=body.url,
                    research_project_id=body.research_project_id,
                    title_override=body.title_override,
                )
            )
        )

    @router.post("/sources/discover")
    async def discover_sources(
        body: DiscoverSourcesBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            lambda: handle_domain_call(
                lambda: ctx["svc"].discover_sources(
                    user_id=ctx["user_id"],
                    organization_id=ctx["organization_id"],
                    query=body.query,
                    connectors=body.connectors,
                    max_per_connector=body.max_per_connector,
                    auto_register_project_id=body.auto_register_project_id,
                )
            )
        )

    @router.post("/evidence/claims", status_code=201)
    async def create_claim(
        body: CreateClaimBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_claim_and_evidence(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                research_project_id=body.research_project_id,
                statement=body.statement,
                claim_type=body.claim_type,
                evidence_links=[e.model_dump() for e in body.evidence_links],
            )
        )

    @router.patch("/evidence/claims/{claim_id}")
    async def patch_claim_status(
        claim_id: str,
        body: PatchClaimStatusBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].update_claim_status(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                claim_id=claim_id,
                status=body.status,
            )
        )

    @router.get("/evidence/graph")
    async def get_evidence_graph(
        project_id: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].get_evidence_graph(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                project_id=project_id,
                scope=scope,
            )
        )

    @router.get("/memories")
    async def list_memories(
        memory_type: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_memories(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                memory_type=memory_type,
            )
        )
        return {"total": len(items), "items": items}

    @router.post("/memories", status_code=201)
    async def create_memory(
        body: CreateMemoryBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_memory_object(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                memory_type=body.memory_type,
                title=body.title,
                content=body.content,
                source_ref=body.source_ref,
                project_id=body.project_id,
                retention_days=body.retention_days,
            )
        )

    @router.get("/memories/search")
    async def search_memories(
        request: Request,
        q: str = Query(..., min_length=1),
        memory_type: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        top_k: int = Query(default=8, ge=1, le=30),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        svc: ApexPlatformService = ctx["svc"]
        org_id: str = ctx["organization_id"]
        container = request.app.state.container
        if container.platform_persistence.active:
            q_vec, backend_name = await asyncio.to_thread(
                lambda: svc.llm_gateway.embed_text_1536_with_live_provider(
                    q,
                    organization_id=org_id,
                    providers_catalog=svc.model_providers.get(org_id, []),
                )
            )
            pg_hits = await container.platform_persistence.pgvector_hybrid_search(
                organization_id=org_id,
                query=q,
                query_embedding=q_vec,
                memory_type=memory_type,
                project_id=project_id,
                top_k=top_k,
            )
            return {
                "query": q,
                "search_engine": f"postgresql_pgvector+{backend_name}",
                "results": pg_hits,
            }

        hits = handle_domain_call(
            lambda: svc.search_memories(
                user_id=ctx["user_id"],
                organization_id=org_id,
                query=q,
                memory_type=memory_type,
                project_id=project_id,
                top_k=top_k,
            )
        )
        return {"query": q, "search_engine": "in_process_hybrid", "results": hits}

    @router.post("/memories/{memory_id}/action")
    async def memory_action(
        memory_id: str,
        body: MemoryActionBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].reindex_or_delete_memory(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                memory_id=memory_id,
                action=body.action,
            )
        )

    @router.get("/datasets")
    async def list_datasets(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_datasets(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )
        return {"items": items}

    @router.post("/datasets", status_code=201)
    async def create_dataset(
        body: CreateDatasetBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_dataset(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                description=body.description,
                category=body.category,
                records=body.records,
            )
        )

    @router.get("/experiments")
    async def list_experiments(
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_experiments(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )
        return {"items": items}

    @router.post("/experiments", status_code=201)
    async def create_experiment(
        body: CreateExperimentBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_and_run_experiment(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                description=body.description,
                dataset_id=body.dataset_id,
                dataset_version=body.dataset_version,
                execution_mode=body.execution_mode,
                agent_id=body.agent_id,
                team_id=body.team_id,
                model_identifier=body.model_identifier,
                parameters=body.parameters,
                research_project_id=body.research_project_id,
            )
        )

    @router.post("/experiments/runs/{experiment_run_id}/reproduce", status_code=201)
    async def reproduce_experiment(
        experiment_run_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].reproduce_experiment_run(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                experiment_run_id=experiment_run_id,
            )
        )

    @router.get("/evaluations")
    async def list_evaluations(
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].list_evaluations(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )

    @router.post("/evaluations/run", status_code=201)
    async def run_benchmark(
        body: RunBenchmarkBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].run_evaluation_benchmark(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                suite_id=body.suite_id,
                label=body.label,
                execution_mode=body.execution_mode,
                agent_id=body.agent_id,
                team_id=body.team_id,
                model_identifier=body.model_identifier,
                baseline_eval_run_id=body.baseline_eval_run_id,
            )
        )

    @router.post("/evaluations/runs/{eval_run_id}/human-review", status_code=201)
    async def submit_human_review(
        eval_run_id: str,
        body: HumanEvalBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].submit_human_evaluation_score(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                evaluation_run_id=eval_run_id,
                category=body.category,
                score=body.score,
                rationale=body.rationale,
            )
        )

    @router.get("/models/providers")
    async def list_models(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_model_providers(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )
        return {"items": items}

    @router.post("/models/providers", status_code=201)
    async def register_model_provider(
        body: RegisterModelProviderBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].register_model_provider(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                payload=body.model_dump(),
            )
        )

    @router.post("/models/providers/{provider_slug}/secret")
    async def configure_provider_secret(
        provider_slug: str,
        body: ConfigureProviderSecretBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].configure_model_provider_secret(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                provider_slug=provider_slug,
                api_key=body.api_key,
                base_url=body.base_url,
            )
        )

    @router.post("/models/test-inference")
    async def test_provider_inference(
        body: TestProviderInferenceBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            lambda: handle_domain_call(
                lambda: ctx["svc"].test_model_provider_inference(
                    user_id=ctx["user_id"],
                    organization_id=ctx["organization_id"],
                    model_identifier=body.model_identifier,
                    prompt=body.prompt,
                )
            )
        )

    @router.get("/local-nodes/devices")
    async def list_devices(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        items = handle_domain_call(
            lambda: ctx["svc"].list_local_node_devices(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )
        return {"items": items}

    @router.post("/local-nodes/devices", status_code=201)
    async def register_device(
        body: RegisterDeviceBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].register_local_node_device(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                allowed_directories=body.allowed_directories,
                can_read=body.can_read,
                can_write=body.can_write,
                can_execute=body.can_execute,
            )
        )

    @router.post("/local-nodes/devices/{device_id}/revoke")
    async def revoke_device(
        device_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].revoke_local_node_device(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                device_id=device_id,
            )
        )

    @router.get("/automation/workflows")
    async def list_workflows(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].list_workflows(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )

    @router.post("/automation/workflows", status_code=201)
    async def create_workflow(
        body: CreateAutomationWorkflowBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].create_and_trigger_workflow(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                name=body.name,
                description=body.description,
                risk_level=body.risk_level,
                require_approval=body.require_approval,
            )
        )

    @router.post("/automation/workflows/runs/{workflow_run_id}/resume")
    async def resume_workflow(
        workflow_run_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].resume_approved_workflow_run(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                workflow_run_id=workflow_run_id,
            )
        )

    @router.get("/governance")
    async def get_governance(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].list_governance_state(
                user_id=ctx["user_id"], organization_id=ctx["organization_id"]
            )
        )

    @router.post("/governance/approvals/{approval_id}/decide")
    async def decide_approval(
        approval_id: str,
        body: DecideApprovalBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].decide_approval(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                approval_id=approval_id,
                decision=body.decision,
                decision_note=body.decision_note,
            )
        )

    @router.post("/governance/emergency-stop")
    async def toggle_emergency_stop(
        body: EmergencyStopBody, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].set_emergency_stop(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                active=body.active,
                reason=body.reason,
            )
        )

    @router.delete("/governance/audit/{entry_id}")
    async def attempt_delete_audit(
        entry_id: str, ctx: dict[str, Any] = Depends(require_auth_context)
    ) -> dict[str, Any]:
        return handle_domain_call(lambda: ctx["svc"].audit.delete_entry(entry_id))

    @router.get("/analytics/summary")
    async def analytics_summary(
        scope: str | None = Query(default=None),
        since: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].get_analytics_summary(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
                since_iso=since,
            )
        )

    @router.get("/analytics/export.csv", response_class=PlainTextResponse)
    async def analytics_export_csv(
        scope: str | None = Query(default=None),
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> str:
        return handle_domain_call(
            lambda: ctx["svc"].export_analytics_csv(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                scope=scope,
            )
        )

    @router.get("/extensions")
    async def list_extensions(ctx: dict[str, Any] = Depends(require_auth_context)) -> dict[str, Any]:
        items = await ctx["svc"].list_extensions_with_health(
            user_id=ctx["user_id"], organization_id=ctx["organization_id"]
        )
        return {"items": items}

    @router.patch("/extensions/{slug}")
    async def toggle_extension(
        slug: str,
        body: ToggleExtensionBody,
        ctx: dict[str, Any] = Depends(require_auth_context),
    ) -> dict[str, Any]:
        return handle_domain_call(
            lambda: ctx["svc"].toggle_extension(
                user_id=ctx["user_id"],
                organization_id=ctx["organization_id"],
                slug=slug,
                enabled=body.enabled,
            )
        )

    return router
