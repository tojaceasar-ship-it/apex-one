"""Unified Multi-Tenant Platform Service for APEX ONE 1.0 (Full Research Edition).

Integrates Identity, RBAC, Agent Factory (AgentDNA), Multi-Agent Orchestration,
Research Engine (13 states), Source Registry, Evidence Graph, Shared Memory,
Experiment & Reproduction Engine, Evaluation & Benchmarks, Model Registry,
Hybrid Local Node Pairing/Revocation, Durable Workflows, R0-R3 Governance,
Tamper-Evident Audit Log, Analytics, and Extension Registry.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apex_one.db.base import utcnow
from apex_one.integrations_hub import (
    check_github_integration_health,
    check_ollama_health,
    check_web_search_health,
    create_integration_placeholders,
)
from apex_one.platform.experiments_and_eval import (
    compute_dataset_checksum,
    execute_controlled_experiment_protocol,
    score_evaluation_run,
)
from apex_one.platform.governance import (
    GovernanceBlockedError,
    RiskLevel,
    TamperEvidentAuditLedger,
    compute_approval_parameter_hash,
    default_approval_expiry,
)
from apex_one.platform.llm_gateway import LLMGateway
from apex_one.platform.local_intelligence import (
    BUILTIN_AGENT_CONTRACTS,
    ExecutionRouter,
    MultiTierDataCache,
    TaskCategory,
    TelemetryLedger,
    analyze_ohlcv_and_volatility,
    analyze_tabular_csv_statistics,
    compute_sha256_integrity,
    fetch_btc_spot_and_ohlcv_http,
    run_monte_carlo_simulation,
    validate_and_parse_structured_data,
)
from apex_one.platform.memory_and_docs import (
    VALID_MEMORY_TYPES,
    chunk_document_text,
    extract_document_text,
    rank_memory_chunks,
)
from apex_one.platform.orchestration import (
    DurableCheckpointRuntimeAdapter,
    diff_agent_dna_versions,
    load_builtin_agent_dna_manifests,
)
from apex_one.platform.research_engine import (
    VALID_CLAIM_STATUSES,
    ResearchState,
    build_evidence_graph_topology,
    evaluate_claim_evidence_status,
    validate_research_state_transition,
)
from apex_one.platform.scientific_connectors import (
    discover_scientific_literature,
    fetch_and_extract_url_source,
    fetch_live_btc_market_telemetry,
)
from apex_one.platform.security import (
    ROLE_PERMISSIONS,
    generate_secure_token,
    hash_password,
    inspect_untrusted_document,
    role_has_permission,
    sha256_hex,
    verify_password,
)


class AuthorizationError(PermissionError):
    """Raised when user lacks membership or RBAC permission in target organization."""


class ResourceNotFoundError(LookupError):
    """Raised when a requested domain entity does not exist inside the organization."""


class ApexPlatformService:
    """Authoritative multi-tenant domain coordinator for APEX ONE 1.0."""

    def __init__(self, *, repo_root: Path | None = None, seed_demo: bool = True) -> None:
        self._repo_root = repo_root or Path(__file__).resolve().parents[3]
        self._runtime = DurableCheckpointRuntimeAdapter()
        self.llm_gateway = LLMGateway()
        self.audit = TamperEvidentAuditLedger()

        # Multi-tenant state stores keyed cleanly with mandatory organization_id checks
        self.users: dict[str, dict[str, Any]] = {}
        self.users_by_email: dict[str, str] = {}
        self.organizations: dict[str, dict[str, Any]] = {}
        self.memberships: list[dict[str, Any]] = []
        self.invitations: dict[str, dict[str, Any]] = {}
        self.sessions_by_hash: dict[str, dict[str, Any]] = {}
        self.api_credentials: dict[str, dict[str, Any]] = {}
        self.devices: dict[str, dict[str, Any]] = {}

        self.agents: dict[str, dict[str, Any]] = {}
        self.agent_versions: dict[str, list[dict[str, Any]]] = {}
        self.agent_teams: dict[str, dict[str, Any]] = {}
        self.agent_runs: dict[str, dict[str, Any]] = {}
        self.tools_catalog: dict[str, list[dict[str, Any]]] = {}

        self.projects: dict[str, dict[str, Any]] = {}
        self.questions: dict[str, list[dict[str, Any]]] = {}
        self.briefs: dict[str, list[dict[str, Any]]] = {}
        self.plans: dict[str, list[dict[str, Any]]] = {}
        self.hypotheses: dict[str, list[dict[str, Any]]] = {}
        self.reports: dict[str, list[dict[str, Any]]] = {}

        self.sources: dict[str, dict[str, Any]] = {}
        self.source_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}

        self.memories: dict[str, dict[str, Any]] = {}
        self.memory_chunks: dict[str, list[dict[str, Any]]] = {}

        self.datasets: dict[str, dict[str, Any]] = {}
        self.dataset_versions: dict[str, list[dict[str, Any]]] = {}
        self.experiments: dict[str, dict[str, Any]] = {}
        self.experiment_runs: dict[str, dict[str, Any]] = {}

        self.eval_suites: dict[str, dict[str, Any]] = {}
        self.eval_cases: dict[str, list[dict[str, Any]]] = {}
        self.eval_runs: dict[str, dict[str, Any]] = {}
        self.eval_scores: dict[str, list[dict[str, Any]]] = {}
        self.regressions: list[dict[str, Any]] = []

        self.workflow_defs: dict[str, dict[str, Any]] = {}
        self.workflow_runs_store: dict[str, dict[str, Any]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.budgets: dict[str, list[dict[str, Any]]] = {}
        self.usage_records: list[dict[str, Any]] = []
        self.model_providers: dict[str, list[dict[str, Any]]] = {}
        self.extensions: dict[str, list[dict[str, Any]]] = {}
        self.l3_data_cache_records: dict[str, dict[str, Any]] = {}
        self.execution_telemetry_log: list[dict[str, Any]] = []

        self.data_cache = MultiTierDataCache(
            l2_file_path=os.environ.get("APEX_L2_CACHE_FILE"),
            l3_backing_dict=self.l3_data_cache_records,
        )
        self.telemetry_ledger = TelemetryLedger(backing_list=self.execution_telemetry_log)
        self.execution_router = ExecutionRouter(
            cache=self.data_cache,
            telemetry=self.telemetry_ledger,
        )

        self.default_org_id: str = ""
        if seed_demo:
            self._bootstrap_workspace_and_demo()

    _CHECKPOINT_FIELDS: tuple[str, ...] = (
        "users",
        "users_by_email",
        "organizations",
        "memberships",
        "invitations",
        "sessions_by_hash",
        "api_credentials",
        "devices",
        "agents",
        "agent_versions",
        "agent_teams",
        "agent_runs",
        "tools_catalog",
        "projects",
        "questions",
        "briefs",
        "plans",
        "hypotheses",
        "reports",
        "sources",
        "source_snapshots",
        "claims",
        "evidence",
        "memories",
        "memory_chunks",
        "datasets",
        "dataset_versions",
        "experiments",
        "experiment_runs",
        "eval_suites",
        "eval_cases",
        "eval_runs",
        "eval_scores",
        "regressions",
        "workflow_defs",
        "workflow_runs_store",
        "approvals",
        "budgets",
        "usage_records",
        "model_providers",
        "extensions",
        "l3_data_cache_records",
        "execution_telemetry_log",
        "default_org_id",
    )

    def save_local_checkpoint(self, file_path: Path) -> None:
        from dataclasses import asdict

        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            k: getattr(self, k) for k in self._CHECKPOINT_FIELDS
        }
        payload["_audit_by_org"] = {
            org_id: [asdict(e) for e in self.audit.get_chronological_entries(org_id)]
            for org_id in self.organizations
        }
        tmp = file_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
        tmp.replace(file_path)

    def load_local_checkpoint(self, file_path: Path) -> bool:
        if not file_path.exists():
            return False
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            for k in self._CHECKPOINT_FIELDS:
                if k in payload:
                    setattr(self, k, payload[k])
            for org_id, entries in (payload.get("_audit_by_org") or {}).items():
                self.audit.hydrate_org_entries(org_id, entries)
            self.data_cache.bind_l3_store(self.l3_data_cache_records)
            self.telemetry_ledger.bind_store(self.execution_telemetry_log)
            return True
        except Exception:
            return False

    # =========================================================================
    # IDENTITY, AUTHENTICATION & STRICT MULTI-TENANT AUTHORIZATION
    # =========================================================================

    def register_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        organization_name: str | None = None,
        locale: str = "pl",
    ) -> dict[str, Any]:
        norm_email = email.strip().lower()
        if not norm_email or "@" not in norm_email:
            raise ValueError("Invalid email address.")
        if norm_email in self.users_by_email:
            raise ValueError("Email is already registered.")

        uid = str(uuid.uuid4())
        now = utcnow().isoformat()
        user = {
            "id": uid,
            "email": norm_email,
            "password_hash": hash_password(password),
            "display_name": display_name.strip() or norm_email.split("@")[0],
            "locale": locale if locale in {"pl", "en"} else "pl",
            "preferences": {"theme": "graphite-dark", "default_scope": "all"},
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        self.users[uid] = user
        self.users_by_email[norm_email] = uid

        if organization_name:
            org = self._create_organization_internal(
                name=organization_name, owner_user_id=uid
            )
            self._provision_org_defaults(org["id"], actor_id=uid, seed_demo=False)
        return self._public_user(user)

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        norm_email = email.strip().lower()
        uid = self.users_by_email.get(norm_email)
        if not uid:
            raise AuthorizationError("Invalid email or password.")
        user = self.users[uid]
        if not user["is_active"] or not verify_password(password, user["password_hash"]):
            raise AuthorizationError("Invalid email or password.")

        user_orgs = self.list_user_organizations(uid)
        if not user_orgs:
            org = self._create_organization_internal(
                name=f"Org {user['display_name']}", owner_user_id=uid
            )
            self._provision_org_defaults(org["id"], actor_id=uid, seed_demo=False)
            user_orgs = self.list_user_organizations(uid)

        primary_org_id = user_orgs[0]["organization_id"]
        raw_token, token_hash = generate_secure_token("apxsess")
        csrf_raw, _ = generate_secure_token("csrf")
        expires_at = (utcnow() + timedelta(hours=12)).isoformat()

        session = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "organization_id": primary_org_id,
            "token_hash": token_hash,
            "csrf_token": csrf_raw,
            "expires_at": expires_at,
            "revoked_at": None,
            "created_at": utcnow().isoformat(),
        }
        self.sessions_by_hash[token_hash] = session
        self.audit.append(
            organization_id=primary_org_id,
            actor_id=uid,
            actor_type="user",
            action="auth.login",
            resource_type="session",
            resource_id=session["id"],
            risk_level="R0",
        )
        return {
            "access_token": raw_token,
            "csrf_token": csrf_raw,
            "expires_at": expires_at,
            "user": self._public_user(user),
            "organizations": user_orgs,
            "active_organization_id": primary_org_id,
        }

    def logout(self, raw_token: str) -> None:
        th = sha256_hex(raw_token)
        sess = self.sessions_by_hash.get(th)
        if sess and sess["revoked_at"] is None:
            sess["revoked_at"] = utcnow().isoformat()
            self.audit.append(
                organization_id=sess["organization_id"],
                actor_id=sess["user_id"],
                actor_type="user",
                action="auth.logout",
                resource_type="session",
                resource_id=sess["id"],
                risk_level="R0",
            )

    def authenticate_token(self, raw_token: str | None) -> dict[str, Any]:
        if not raw_token:
            raise AuthorizationError("Authentication required.")
        th = sha256_hex(raw_token)
        # Check interactive session first
        sess = self.sessions_by_hash.get(th)
        if sess is not None:
            if sess["revoked_at"] is not None:
                raise AuthorizationError("Session has been logged out or revoked.")
            if datetime.fromisoformat(sess["expires_at"]) <= utcnow():
                raise AuthorizationError("Session has expired.")
            user = self.users.get(sess["user_id"])
            if not user or not user["is_active"]:
                raise AuthorizationError("User account is inactive.")
            return {
                "user": self._public_user(user),
                "default_organization_id": sess["organization_id"],
                "csrf_token": sess["csrf_token"],
                "auth_type": "session",
            }

        # Check API credential key
        cred = self.api_credentials.get(th)
        if cred is not None and cred["revoked_at"] is None:
            user = self.users.get(cred["user_id"])
            if user and user["is_active"]:
                cred["last_used_at"] = utcnow().isoformat()
                return {
                    "user": self._public_user(user),
                    "default_organization_id": cred["organization_id"],
                    "csrf_token": None,
                    "auth_type": "api_key",
                }
        raise AuthorizationError("Invalid or expired authentication token.")

    def authorize(
        self,
        *,
        user_id: str,
        organization_id: str,
        required_permission: str = "read:all",
        actor_type: str = "user",
    ) -> dict[str, Any]:
        """Verify membership in `organization_id` and enforce RBAC permission.

        Never trusts a browser-supplied organization_id without membership check,
        and forbids agents from altering authorization policies.
        """
        if actor_type == "agent" and required_permission in {"org:manage", "users:manage", "governance:approve_r2", "governance:approve_r3"}:
            raise AuthorizationError(
                "Agents are permanently forbidden from altering authorization or approving their own actions."
            )

        org = self.organizations.get(organization_id)
        if org is None:
            raise AuthorizationError("Access denied: unknown or unauthorized organization.")

        membership = next(
            (
                m
                for m in self.memberships
                if m["organization_id"] == organization_id
                and m["user_id"] == user_id
                and m["status"] == "active"
            ),
            None,
        )
        if membership is None:
            raise AuthorizationError(
                "Cross-tenant access denied: user is not an active member of target organization."
            )

        role = membership["role_name"]
        if not role_has_permission(role, required_permission):
            raise AuthorizationError(
                f"Role '{role}' lacks required permission '{required_permission}'."
            )
        return {"organization": org, "membership": membership, "role": role}

    def list_user_organizations(self, user_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for m in self.memberships:
            if m["user_id"] == user_id and m["status"] == "active":
                org = self.organizations.get(m["organization_id"])
                if org:
                    result.append(
                        {
                            "organization_id": org["id"],
                            "slug": org["slug"],
                            "name": org["name"],
                            "role": m["role_name"],
                            "emergency_stop_active": org["emergency_stop_active"],
                        }
                    )
        return result

    def create_organization(
        self, *, user_id: str, name: str
    ) -> dict[str, Any]:
        org = self._create_organization_internal(name=name, owner_user_id=user_id)
        self._provision_org_defaults(org["id"], actor_id=user_id, seed_demo=False)
        return org

    def create_invitation(
        self,
        *,
        user_id: str,
        organization_id: str,
        email: str,
        role_name: str,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="users:manage",
        )
        if role_name not in ROLE_PERMISSIONS:
            raise ValueError(f"Unsupported role '{role_name}'.")
        raw_tok, tok_hash = generate_secure_token("apxinv")
        inv = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "email": email.strip().lower(),
            "role_name": role_name,
            "token_preview": raw_tok[:14] + "...",
            "invite_token_once": raw_tok,
            "token_hash": tok_hash,
            "invited_by": user_id,
            "status": "pending",
            "expires_at": (utcnow() + timedelta(days=7)).isoformat(),
            "created_at": utcnow().isoformat(),
        }
        self.invitations[inv["id"]] = inv
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="org.invite_member",
            resource_type="invitation",
            resource_id=inv["id"],
            risk_level="R2",
            details={"email": inv["email"], "role": role_name},
        )
        return {k: v for k, v in inv.items() if k != "token_hash"}

    def create_api_credential(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="read:all",
        )
        raw_key, key_hash = generate_secure_token("apxkey")
        rec = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "user_id": user_id,
            "name": name.strip() or "API Key",
            "key_prefix": raw_key[:12],
            "scopes": scopes or ["read:all"],
            "created_at": utcnow().isoformat(),
            "last_used_at": None,
            "revoked_at": None,
        }
        self.api_credentials[key_hash] = rec
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="identity.create_api_credential",
            resource_type="api_credential",
            resource_id=rec["id"],
            risk_level="R2",
        )
        return {**rec, "secret_key_once": raw_key}

    def update_user_preferences(
        self,
        *,
        user_id: str,
        display_name: str | None = None,
        locale: str | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user = self.users.get(user_id)
        if not user:
            raise ResourceNotFoundError("User not found.")
        if display_name:
            user["display_name"] = display_name.strip()
        if locale in {"pl", "en"}:
            user["locale"] = locale
        if preferences is not None:
            user["preferences"] = {**user["preferences"], **preferences}
        user["updated_at"] = utcnow().isoformat()
        return self._public_user(user)

    # =========================================================================
    # AGENT FACTORY & VERSIONED AgentDNA
    # =========================================================================

    def list_agents(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        items = [
            a
            for a in self.agents.values()
            if a["organization_id"] == organization_id
            and (scope in {None, "all"} or a["workspace_scope"] == scope)
        ]
        return sorted(items, key=lambda x: x["name"])

    def get_agent_detail(
        self, *, user_id: str, organization_id: str, agent_id: str
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        ag = self._require_org_entity(self.agents, agent_id, organization_id, "Agent")
        versions = list(self.agent_versions.get(agent_id, []))
        runs = [
            r
            for r in self.agent_runs.values()
            if r["organization_id"] == organization_id and r.get("agent_id") == agent_id
        ]
        return {**ag, "versions": versions, "recent_runs": runs[-10:]}

    def create_agent(
        self,
        *,
        user_id: str,
        organization_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:write",
        )
        aid = str(uuid.uuid4())
        now = utcnow().isoformat()
        slug = (
            payload.get("slug")
            or payload["name"].lower().replace(" ", "-")[:40] + "-" + aid[:6]
        )
        dna = self._normalize_agent_dna(payload)
        agent = {
            "id": aid,
            "organization_id": organization_id,
            "slug": slug,
            "name": payload["name"],
            "description": payload.get("description", ""),
            "role": payload.get("role", "specialist"),
            "purpose": payload.get("purpose", ""),
            "current_version": 1,
            "status": payload.get("status", "active"),
            "workspace_scope": payload.get("workspace_scope", "research"),
            "dna": dna,
            "created_at": now,
            "updated_at": now,
        }
        self.agents[aid] = agent
        self.agent_versions[aid] = [
            {
                "id": str(uuid.uuid4()),
                "agent_id": aid,
                "version": 1,
                "dna_json": dna,
                "change_summary": "Initial AgentDNA v1",
                "created_by": user_id,
                "created_at": now,
            }
        ]
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="agent.create",
            resource_type="agent",
            resource_id=aid,
            risk_level="R1",
            details={"name": agent["name"], "version": 1},
        )
        return agent

    def update_agent_creates_new_version(
        self,
        *,
        user_id: str,
        organization_id: str,
        agent_id: str,
        updates: dict[str, Any],
        change_summary: str = "Updated AgentDNA configuration",
    ) -> dict[str, Any]:
        """Modify an agent by appending an immutable new AgentDNA version (never overwrites history)."""
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:write",
        )
        agent = self._require_org_entity(self.agents, agent_id, organization_id, "Agent")
        merged_source = {**agent["dna"], **agent, **updates}
        new_dna = self._normalize_agent_dna(merged_source)
        next_ver = int(agent["current_version"]) + 1
        now = utcnow().isoformat()

        agent["name"] = updates.get("name", agent["name"])
        agent["description"] = updates.get("description", agent["description"])
        agent["role"] = updates.get("role", agent["role"])
        agent["purpose"] = updates.get("purpose", agent["purpose"])
        agent["status"] = updates.get("status", agent["status"])
        agent["current_version"] = next_ver
        agent["dna"] = new_dna
        agent["updated_at"] = now

        self.agent_versions.setdefault(agent_id, []).append(
            {
                "id": str(uuid.uuid4()),
                "agent_id": agent_id,
                "version": next_ver,
                "dna_json": new_dna,
                "change_summary": change_summary,
                "created_by": user_id,
                "created_at": now,
            }
        )
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="agent.new_dna_version",
            resource_type="agent",
            resource_id=agent_id,
            risk_level="R2",
            details={"version": next_ver, "change_summary": change_summary},
        )
        return agent

    def duplicate_agent(
        self, *, user_id: str, organization_id: str, agent_id: str
    ) -> dict[str, Any]:
        orig = self.get_agent_detail(
            user_id=user_id, organization_id=organization_id, agent_id=agent_id
        )
        return self.create_agent(
            user_id=user_id,
            organization_id=organization_id,
            payload={
                **orig["dna"],
                "name": f"{orig['name']} (Kopia)",
                "description": orig["description"],
                "role": orig["role"],
                "purpose": orig["purpose"],
                "workspace_scope": "research",
            },
        )

    def set_agent_status(
        self, *, user_id: str, organization_id: str, agent_id: str, status: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:write",
        )
        if status not in {"active", "inactive", "archived"}:
            raise ValueError("Invalid agent status.")
        ag = self._require_org_entity(self.agents, agent_id, organization_id, "Agent")
        ag["status"] = status
        ag["updated_at"] = utcnow().isoformat()
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action=f"agent.status.{status}",
            resource_type="agent",
            resource_id=agent_id,
            risk_level="R1",
        )
        return ag

    def compare_agent_versions(
        self,
        *,
        user_id: str,
        organization_id: str,
        agent_id: str,
        version_a: int,
        version_b: int,
    ) -> dict[str, Any]:
        detail = self.get_agent_detail(
            user_id=user_id, organization_id=organization_id, agent_id=agent_id
        )
        ver_map = {int(v["version"]): v["dna_json"] for v in detail["versions"]}
        if version_a not in ver_map or version_b not in ver_map:
            raise ResourceNotFoundError("Requested AgentDNA version not found.")
        return {
            "agent_id": agent_id,
            "version_a": version_a,
            "version_b": version_b,
            "differences": diff_agent_dna_versions(ver_map[version_a], ver_map[version_b]),
        }

    # =========================================================================
    # AGENT TEAMS & MULTI-AGENT ORCHESTRATION RUNS
    # =========================================================================

    def list_teams(self, *, user_id: str, organization_id: str) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        return [
            t
            for t in self.agent_teams.values()
            if t["organization_id"] == organization_id
        ]

    def create_team(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        description: str,
        supervisor_agent_id: str | None,
        member_agent_ids: list[str],
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:write",
        )
        tid = str(uuid.uuid4())
        team = {
            "id": tid,
            "organization_id": organization_id,
            "name": name,
            "description": description,
            "supervisor_agent_id": supervisor_agent_id,
            "member_agent_ids": member_agent_ids,
            "coordination_policy": {"mode": "dag_supervised", "conflict_gate": True},
            "workspace_scope": "research",
            "created_at": utcnow().isoformat(),
        }
        self.agent_teams[tid] = team
        return team

    def launch_agent_run(
        self,
        *,
        user_id: str,
        organization_id: str,
        objective: str,
        execution_mode: str = "multi_agent_team",
        research_project_id: str | None = None,
        agent_id: str | None = None,
        team_id: str | None = None,
        interrupt_after_step: int | None = None,
        workspace_scope: str = "research",
        model_identifier: str = "apex-analytical-local-v1",
        auto_discover_sources: bool = False,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:run",
        )
        self._enforce_operational_guards(organization_id)

        if auto_discover_sources:
            self.auto_ingest_discovered_sources(
                user_id=user_id,
                organization_id=organization_id,
                query=objective,
                research_project_id=research_project_id,
                workspace_scope=workspace_scope,
            )

        initial_run = self._create_initial_run_record(
            organization_id=organization_id,
            objective=objective,
            execution_mode=execution_mode,
            research_project_id=research_project_id,
            agent_id=agent_id,
            team_id=team_id,
            workspace_scope=workspace_scope,
            model_identifier=model_identifier,
        )
        run_id = initial_run["id"]
        project_sources, agents_by_slug = self._resolve_run_context(
            organization_id=organization_id,
            research_project_id=research_project_id,
        )

        completed_run = self._runtime.execute_or_resume_run(
            run_record=initial_run,
            agents_lookup=agents_by_slug,
            project_sources=project_sources,
            interrupt_after_step=interrupt_after_step,
            llm_gateway=self.llm_gateway,
            organization_id=organization_id,
            providers_catalog=self._ensure_lmstudio_lan_provider(organization_id),
            execution_router=self.execution_router,
        )
        self.agent_runs[run_id] = completed_run
        self._persist_run_side_effects(completed_run, user_id=user_id)
        return completed_run

    def enqueue_async_run(
        self,
        *,
        user_id: str,
        organization_id: str,
        objective: str,
        execution_mode: str = "multi_agent_team",
        research_project_id: str | None = None,
        agent_id: str | None = None,
        team_id: str | None = None,
        workspace_scope: str = "research",
        model_identifier: str = "apex-analytical-local-v1",
        auto_discover_sources: bool = False,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:run",
        )
        self._enforce_operational_guards(organization_id)
        if auto_discover_sources:
            self.auto_ingest_discovered_sources(
                user_id=user_id,
                organization_id=organization_id,
                query=objective,
                research_project_id=research_project_id,
                workspace_scope=workspace_scope,
            )
        initial_run = self._create_initial_run_record(
            organization_id=organization_id,
            objective=objective,
            execution_mode=execution_mode,
            research_project_id=research_project_id,
            agent_id=agent_id,
            team_id=team_id,
            workspace_scope=workspace_scope,
            model_identifier=model_identifier,
        )
        initial_run["status"] = "RUNNING"
        self.agent_runs[initial_run["id"]] = initial_run
        return initial_run

    def advance_run_one_step(
        self,
        *,
        organization_id: str,
        user_id: str,
        run_id: str,
        step_limit: int,
        total_steps: int,
    ) -> dict[str, Any]:
        run_rec = self._require_org_entity(
            self.agent_runs, run_id, organization_id, "AgentRun"
        )
        project_sources, agents_by_slug = self._resolve_run_context(
            organization_id=organization_id,
            research_project_id=run_rec.get("research_project_id"),
        )
        stop_after = step_limit if step_limit < total_steps else None
        advanced = self._runtime.execute_or_resume_run(
            run_record=run_rec,
            agents_lookup=agents_by_slug,
            project_sources=project_sources,
            interrupt_after_step=stop_after,
            llm_gateway=self.llm_gateway,
            organization_id=organization_id,
            providers_catalog=self._ensure_lmstudio_lan_provider(organization_id),
            execution_router=self.execution_router,
        )
        if stop_after is not None and advanced["status"] == "INTERRUPTED":
            advanced["status"] = "RUNNING"
        self.agent_runs[run_id] = advanced
        if advanced["status"] == "COMPLETED":
            self._persist_run_side_effects(advanced, user_id=user_id)
        return advanced

    def _create_initial_run_record(
        self,
        *,
        organization_id: str,
        objective: str,
        execution_mode: str,
        research_project_id: str | None,
        agent_id: str | None,
        team_id: str | None,
        workspace_scope: str,
        model_identifier: str,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utcnow().isoformat()
        agent_ver = None
        if agent_id and agent_id in self.agents:
            ag = self._require_org_entity(self.agents, agent_id, organization_id, "Agent")
            if ag["status"] != "active":
                raise ValueError("Cannot launch run on inactive or archived agent.")
            agent_ver = ag["current_version"]
        return {
            "id": run_id,
            "organization_id": organization_id,
            "research_project_id": research_project_id,
            "agent_id": agent_id,
            "agent_version": agent_ver,
            "team_id": team_id,
            "execution_mode": execution_mode,
            "model_identifier": model_identifier,
            "runtime_adapter": self._runtime.adapter_name,
            "objective": objective,
            "status": "RUNNING",
            "progress_pct": 0,
            "checkpoint_json": {},
            "conflicts_json": [],
            "output_json": None,
            "steps": [],
            "tool_invocations": [],
            "tokens_used": 0,
            "cost_usd": 0.0,
            "duration_ms": 0,
            "workspace_scope": workspace_scope,
            "started_at": now,
            "completed_at": None,
            "created_at": now,
        }

    def _resolve_run_context(
        self,
        *,
        organization_id: str,
        research_project_id: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        project_sources = sorted(
            [
                s
                for s in self.sources.values()
                if s["organization_id"] == organization_id
                and (
                    research_project_id is None
                    or s.get("research_project_id") == research_project_id
                )
            ],
            key=lambda x: str(x.get("created_at") or ""),
            reverse=True,
        )
        agents_by_slug: dict[str, dict[str, Any]] = {}
        for a in self.agents.values():
            if a["organization_id"] != organization_id:
                continue
            vers = self.agent_versions.get(a["id"], [])
            latest_dna = vers[-1]["dna_json"] if vers else {}
            agents_by_slug[a["slug"]] = {**a, "dna": latest_dna}
        return project_sources, agents_by_slug

    def resume_interrupted_agent_run(
        self, *, user_id: str, organization_id: str, run_id: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:run",
        )
        self._enforce_operational_guards(organization_id)
        run_rec = self._require_org_entity(
            self.agent_runs, run_id, organization_id, "AgentRun"
        )
        if run_rec["status"] not in {"INTERRUPTED", "FAILED"}:
            return run_rec

        project_sources, agents_by_slug = self._resolve_run_context(
            organization_id=organization_id,
            research_project_id=run_rec.get("research_project_id"),
        )
        resumed = self._runtime.execute_or_resume_run(
            run_record=run_rec,
            agents_lookup=agents_by_slug,
            project_sources=project_sources,
            interrupt_after_step=None,
            llm_gateway=self.llm_gateway,
            organization_id=organization_id,
            providers_catalog=self._ensure_lmstudio_lan_provider(organization_id),
            execution_router=self.execution_router,
        )
        self.agent_runs[run_id] = resumed
        self._persist_run_side_effects(resumed, user_id=user_id)
        return resumed

    def cancel_agent_run(
        self, *, user_id: str, organization_id: str, run_id: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="agents:run",
        )
        run_rec = self._require_org_entity(
            self.agent_runs, run_id, organization_id, "AgentRun"
        )
        run_rec["status"] = "CANCELLED"
        run_rec["completed_at"] = utcnow().isoformat()
        return run_rec

    def list_agent_runs(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        runs = [
            r
            for r in self.agent_runs.values()
            if r["organization_id"] == organization_id
            and (scope in {None, "all"} or r["workspace_scope"] == scope)
        ]
        return sorted(runs, key=lambda r: r["created_at"], reverse=True)

    # =========================================================================
    # RESEARCH ENGINE (13 VALIDATED STATES), SOURCES & EVIDENCE GRAPH
    # =========================================================================

    def create_research_project(
        self,
        *,
        user_id: str,
        organization_id: str,
        title: str,
        hypothesis: str = "",
        scope_summary: str = "",
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="research:write",
        )
        pid = str(uuid.uuid4())
        now = utcnow().isoformat()
        proj = {
            "id": pid,
            "organization_id": organization_id,
            "title": title,
            "hypothesis": hypothesis,
            "state": ResearchState.CREATED.value,
            "version": 1,
            "workspace_scope": workspace_scope,
            "scope_summary": scope_summary,
            "tasks": [
                {"id": str(uuid.uuid4()), "title": "Zdefiniuj Research Brief", "status": "todo"},
                {"id": str(uuid.uuid4()), "title": "Zarejestruj źródła pierwotne", "status": "todo"},
                {"id": str(uuid.uuid4()), "title": "Przeprowadź ekstrakcję dowodów", "status": "todo"},
            ],
            "created_by": user_id,
            "created_at": now,
            "updated_at": now,
        }
        self.projects[pid] = proj
        if hypothesis:
            self.hypotheses[pid] = [
                {
                    "id": str(uuid.uuid4()),
                    "research_project_id": pid,
                    "statement": hypothesis,
                    "rationale": "Hipoteza początkowa projektu",
                    "status": "proposed",
                    "created_at": now,
                }
            ]
        self.briefs[pid] = [
            {
                "id": str(uuid.uuid4()),
                "research_project_id": pid,
                "version": 1,
                "objective": title,
                "scope_summary": scope_summary,
                "methodology": "Analiza źródłowa oparta na dowodach i testach replikacyjnych.",
                "expected_deliverables": "Graf dowodowy oraz raport z referencjami.",
                "created_at": now,
            }
        ]
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="research.create_project",
            resource_type="research_project",
            resource_id=pid,
            risk_level="R1",
            details={"title": title, "scope": workspace_scope},
        )
        return proj

    def transition_research_project_state(
        self,
        *,
        user_id: str,
        organization_id: str,
        project_id: str,
        target_state: str,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="research:write",
        )
        proj = self._require_org_entity(
            self.projects, project_id, organization_id, "ResearchProject"
        )
        validated = validate_research_state_transition(proj["state"], target_state)
        proj["state"] = validated.value
        proj["version"] = int(proj["version"]) + 1
        proj["updated_at"] = utcnow().isoformat()
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="research.transition_state",
            resource_type="research_project",
            resource_id=project_id,
            risk_level="R1",
            details={"target_state": validated.value},
        )
        return proj

    def upsert_research_brief_and_questions(
        self,
        *,
        user_id: str,
        organization_id: str,
        project_id: str,
        objective: str,
        scope_summary: str,
        methodology: str,
        expected_deliverables: str,
        questions: list[str] | None = None,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="research:write",
        )
        proj = self._require_org_entity(
            self.projects, project_id, organization_id, "ResearchProject"
        )
        existing = self.briefs.setdefault(project_id, [])
        next_v = len(existing) + 1
        now = utcnow().isoformat()
        brief = {
            "id": str(uuid.uuid4()),
            "research_project_id": project_id,
            "version": next_v,
            "objective": objective,
            "scope_summary": scope_summary,
            "methodology": methodology,
            "expected_deliverables": expected_deliverables,
            "created_at": now,
        }
        existing.append(brief)
        proj["scope_summary"] = scope_summary
        proj["version"] = int(proj["version"]) + 1
        proj["updated_at"] = now

        if questions is not None:
            self.questions[project_id] = [
                {
                    "id": str(uuid.uuid4()),
                    "research_project_id": project_id,
                    "question_text": q,
                    "priority": idx,
                    "status": "open",
                    "created_at": now,
                }
                for idx, q in enumerate(questions, start=1)
                if q.strip()
            ]
        return self.get_research_project_detail(
            user_id=user_id, organization_id=organization_id, project_id=project_id
        )

    def get_research_project_detail(
        self, *, user_id: str, organization_id: str, project_id: str
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        proj = self._require_org_entity(
            self.projects, project_id, organization_id, "ResearchProject"
        )
        proj_sources = [
            s
            for s in self.sources.values()
            if s["organization_id"] == organization_id
            and s.get("research_project_id") == project_id
        ]
        proj_claims = [
            c
            for c in self.claims.values()
            if c["organization_id"] == organization_id
            and c.get("research_project_id") == project_id
        ]
        proj_runs = [
            r
            for r in self.agent_runs.values()
            if r["organization_id"] == organization_id
            and r.get("research_project_id") == project_id
        ]
        return {
            **proj,
            "briefs": self.briefs.get(project_id, []),
            "questions": self.questions.get(project_id, []),
            "plans": self.plans.get(project_id, []),
            "hypotheses": self.hypotheses.get(project_id, []),
            "sources": proj_sources,
            "claims": proj_claims,
            "reports": self.reports.get(project_id, []),
            "runs": proj_runs,
        }

    def list_research_projects(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        items = [
            p
            for p in self.projects.values()
            if p["organization_id"] == organization_id
            and (scope in {None, "all"} or p["workspace_scope"] == scope)
        ]
        return sorted(items, key=lambda x: x["created_at"], reverse=True)

    # =========================================================================
    # SOURCE REGISTRY & EVIDENCE GRAPH
    # =========================================================================

    def register_source(
        self,
        *,
        user_id: str,
        organization_id: str,
        source_type: str,
        title: str,
        uri_or_filename: str,
        raw_text: str | None = None,
        content_base64: str | None = None,
        research_project_id: str | None = None,
        authors: list[str] | None = None,
        published_at: str | None = None,
        copyright_notice: str = "Source attribution preserved.",
        metadata_json: dict[str, Any] | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="sources:write",
        )
        stype = source_type.upper().strip()
        if stype not in {"URL", "PDF", "DOCX", "TXT", "MD"}:
            raise ValueError(f"Unsupported source_type '{source_type}'.")

        extracted_text, raw_bytes = extract_document_text(
            source_type=stype,
            raw_text=raw_text,
            content_base64=content_base64,
        )
        safety = inspect_untrusted_document(extracted_text)
        checksum = sha256_hex(raw_bytes)

        # Duplicate detection within organization + project
        existing = next(
            (
                s
                for s in self.sources.values()
                if s["organization_id"] == organization_id
                and s["checksum_sha256"] == checksum
                and s.get("research_project_id") == research_project_id
            ),
            None,
        )
        if existing is not None:
            return {**existing, "duplicate_detected": True}

        sid = str(uuid.uuid4())
        now = utcnow().isoformat()
        # Never claim an external URL was live-checked unless content was explicitly provided
        verification_status = (
            "flagged_untrusted_content"
            if not safety.safe
            else ("unverified_external_url" if stype == "URL" and not extracted_text else "verified_checksum")
        )
        meta = {**(metadata_json or {}), "safety_flags": safety.flags}
        src = {
            "id": sid,
            "organization_id": organization_id,
            "research_project_id": research_project_id,
            "source_type": stype,
            "title": title,
            "uri_or_filename": uri_or_filename,
            "authors": authors or [],
            "published_at": published_at,
            "retrieved_at": now,
            "checksum_sha256": checksum,
            "version": 1,
            "verification_status": verification_status,
            "copyright_notice": copyright_notice,
            "extracted_text": safety.sanitized_text,
            "metadata_json": meta,
            "workspace_scope": workspace_scope,
            "duplicate_detected": False,
        }
        self.sources[sid] = src
        self.source_snapshots[sid] = [
            {
                "id": str(uuid.uuid4()),
                "source_id": sid,
                "version": 1,
                "checksum_sha256": checksum,
                "byte_size": len(raw_bytes),
                "captured_at": now,
            }
        ]
        # Automatically index document into Shared Memory (`document` memory type)
        if safety.sanitized_text:
            self.create_memory_object(
                user_id=user_id,
                organization_id=organization_id,
                memory_type="document",
                title=f"Źródło: {title}",
                content=safety.sanitized_text,
                source_ref=f"source://{sid}/{uri_or_filename}",
                project_id=research_project_id,
                workspace_scope=workspace_scope,
            )
        return src

    def list_sources(
        self,
        *,
        user_id: str,
        organization_id: str,
        project_id: str | None = None,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        return [
            s
            for s in self.sources.values()
            if s["organization_id"] == organization_id
            and (project_id is None or s.get("research_project_id") == project_id)
            and (scope in {None, "all"} or s["workspace_scope"] == scope)
        ]

    def fetch_and_register_url_source(
        self,
        *,
        user_id: str,
        organization_id: str,
        url: str,
        research_project_id: str | None = None,
        title_override: str | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        """Fetch remote URL via SSRF-safe HTTP client, sanitize, snapshot SHA-256 & index."""
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="sources:write",
        )
        fetched = fetch_and_extract_url_source(url)
        return self.register_source(
            user_id=user_id,
            organization_id=organization_id,
            source_type="URL",
            title=title_override or fetched["title"],
            uri_or_filename=fetched["final_url"],
            raw_text=fetched["extracted_text"],
            research_project_id=research_project_id,
            metadata_json={
                "live_fetched": True,
                "content_type": fetched["content_type"],
                "byte_size": fetched["byte_size"],
                "raw_checksum_sha256": fetched["checksum_sha256"],
            },
            workspace_scope=workspace_scope,
        )

    def discover_sources(
        self,
        *,
        user_id: str,
        organization_id: str,
        query: str,
        connectors: list[str] | None = None,
        max_per_connector: int = 5,
        auto_register_project_id: str | None = None,
        workspace_scope: str = "research",
        ttl_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Cache-first search of external scientific literature APIs (Layer B)."""
        self.authorize(user_id=user_id, organization_id=organization_id)
        conn_key = ",".join(sorted(connectors or ["arxiv", "openalex", "crossref", "github"]))
        cache_key = f"lit_discovery:{organization_id}:{query.strip().lower()}:{conn_key}:{max_per_connector}"
        outcome, _tel, _dec = self.execution_router.fetch_public_data_controlled(
            agent_name="source-scout",
            task_id=f"lit:{uuid.uuid4()}",
            task_category=TaskCategory.PUBLIC_LITERATURE_FETCH,
            cache_key=cache_key,
            provider_name=conn_key,
            ttl_seconds=ttl_seconds,
            fetcher_fn=lambda: discover_scientific_literature(
                query=query,
                connectors=connectors,
                max_per_connector=max_per_connector,
            ),
            validator_fn=lambda d: isinstance(d, dict) and "results" in d,
            retries=1,
        )
        discovery = outcome["data"] or {
            "query": query,
            "count": 0,
            "total_found": 0,
            "providers": {},
            "connector_statuses": {},
            "items": [],
            "results": [],
        }
        registered: list[dict[str, Any]] = []
        if auto_register_project_id:
            self.authorize(
                user_id=user_id,
                organization_id=organization_id,
                required_permission="sources:write",
            )
            existing_uris = {
                str(s.get("uri") or "")
                for s in self.sources.values()
                if s.get("organization_id") == organization_id
                and s.get("research_project_id") == auto_register_project_id
            }
            for item in discovery["results"]:
                if not item.get("abstract"):
                    continue
                uri_val = item["url"] or f"https://scholar.local/{item['connector']}"
                if uri_val in existing_uris:
                    continue
                src = self.register_source(
                    user_id=user_id,
                    organization_id=organization_id,
                    source_type="URL",
                    title=item["title"],
                    uri_or_filename=uri_val,
                    raw_text=f"{item['title']}\n\n{item['abstract']}",
                    research_project_id=auto_register_project_id,
                    authors=item.get("authors", []),
                    published_at=item.get("published_at"),
                    metadata_json={
                        "connector": item["connector"],
                        "doi": item.get("doi"),
                        "citation_count": item.get("citation_count", 0),
                    },
                    workspace_scope=workspace_scope,
                )
                existing_uris.add(uri_val)
                registered.append(src)
        return {**discovery, "cache_hit": outcome.get("cache_hit", False), "registered_sources": registered}

    def fetch_btc_market_data_controlled(
        self,
        *,
        user_id: str,
        organization_id: str,
        ttl_seconds: int = 300,
        max_age_seconds: float | None = None,
        market_api_url: str | None = None,
        allow_stale_on_error: bool = True,
    ) -> dict[str, Any]:
        """Cache-first Layer B acquisition of BTC/USD spot & 90D OHLCV (zero LLM)."""
        self.authorize(user_id=user_id, organization_id=organization_id)
        cache_key = f"btc_ohlcv_90d:{organization_id}"
        outcome, tel, decision = self.execution_router.fetch_public_data_controlled(
            agent_name="source-scout",
            task_id=f"btc_fetch:{uuid.uuid4()}",
            task_category=TaskCategory.PUBLIC_MARKET_DATA_FETCH,
            cache_key=cache_key,
            provider_name="binance_spot_ohlcv",
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
            allow_stale_on_error=allow_stale_on_error,
            fetcher_fn=lambda: fetch_btc_spot_and_ohlcv_http(market_api_url=market_api_url),
            validator_fn=lambda d: (
                isinstance(d, dict)
                and float(d.get("spot_price") or 0.0) > 0.0
                and isinstance(d.get("bars"), list)
                and len(d["bars"]) >= 3
            ),
        )
        return {
            **outcome,
            "telemetry": tel.to_dict(),
            "routing_decision": decision.to_dict(),
        }

    def auto_ingest_discovered_sources(
        self,
        *,
        user_id: str,
        organization_id: str,
        query: str,
        research_project_id: str | None,
        workspace_scope: str = "research",
    ) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        q_low = query.lower()
        if "bitcoin" in q_low or "btc" in q_low:
            btc_outcome = self.fetch_btc_market_data_controlled(
                user_id=user_id,
                organization_id=organization_id,
                ttl_seconds=300,
            )
            btc_data = btc_outcome.get("data")
            if isinstance(btc_data, dict) and btc_data.get("bars"):
                bars = btc_data["bars"]
                vol_stats = analyze_ohlcv_and_volatility(bars, window=min(30, len(bars) - 1))
                md_text = (
                    f"# Zweryfikowane Dane Rynkowe BTC/USD (Cache-First Layer B)\n"
                    f"- Kurs spot BTC/USD: ${vol_stats['spot_price']:,.2f}\n"
                    f"- Zmiana 7D: {vol_stats['return_7d_pct']:+.2f}% | Zmiana 30D: {vol_stats['return_30d_pct']:+.2f}%\n"
                    f"- Zmienność zrealizowana 30D (annualizowana): {vol_stats['realized_volatility_annual_pct']:.2f}%\n"
                    f"- Liczba świec dziennych OHLCV: {vol_stats['bars_analyzed']}\n"
                )
                digest = compute_sha256_integrity(md_text)["sha256"]
                already = next(
                    (
                        s
                        for s in self.sources.values()
                        if s.get("organization_id") == organization_id
                        and s.get("checksum_sha256") == digest
                    ),
                    None,
                )
                if already is None:
                    src = self.register_source(
                        user_id=user_id,
                        organization_id=organization_id,
                        source_type="MD",
                        title="Live BTC/USD OHLCV & Volatility Feed",
                        uri_or_filename="btc_usd_live_30d_telemetry.md",
                        raw_text=md_text,
                        research_project_id=research_project_id,
                        metadata_json={
                            "ohlcv_bars": bars,
                            "cache_envelope": btc_outcome.get("envelope"),
                        },
                        workspace_scope=workspace_scope,
                    )
                    registered.append(src)
            else:
                btc_snap = fetch_live_btc_market_telemetry()
                if btc_snap is not None:
                    src = self.register_source(
                        user_id=user_id,
                        organization_id=organization_id,
                        source_type="MD",
                        title=btc_snap["title"],
                        uri_or_filename="btc_usd_live_30d_telemetry.md",
                        raw_text=btc_snap["markdown"],
                        research_project_id=research_project_id,
                        workspace_scope=workspace_scope,
                    )
                    registered.append(src)
            scholar_q = "Bitcoin volatility drawdown 30-day GARCH prediction"
        else:
            scholar_q = query

        res = self.discover_sources(
            user_id=user_id,
            organization_id=organization_id,
            query=scholar_q,
            connectors=["arxiv", "openalex", "github"],
            max_per_connector=3,
            auto_register_project_id=research_project_id,
            workspace_scope=workspace_scope,
        )
        registered.extend(res.get("registered_sources", []))
        return registered

    def get_local_first_telemetry(
        self,
        *,
        user_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        """Return mandatory aggregate & recent step-level Local-First telemetry (Section 7)."""
        self.authorize(user_id=user_id, organization_id=organization_id)
        summary = self.telemetry_ledger.summarize()
        return {
            "architecture_policy": "LOCAL-FIRST. EXTERNAL API ONLY WHEN JUSTIFIED.",
            "summary": summary,
            "recent_steps": list(reversed(self.execution_telemetry_log[-50:])),
            "cache_records_count": len(self.l3_data_cache_records),
            "agent_contracts": {
                slug: contract.model_dump()
                for slug, contract in BUILTIN_AGENT_CONTRACTS.items()
            },
        }

    def execute_local_first_task(
        self,
        *,
        user_id: str,
        organization_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an explicit Layer A / Layer B task through `ExecutionRouter`."""
        self.authorize(user_id=user_id, organization_id=organization_id)
        op = operation.strip().lower()
        if op == "sha256":
            res, tel, dec = self.execution_router.execute_local_task(
                agent_name="local-engine",
                task_id=f"sha256:{uuid.uuid4()}",
                task_category=TaskCategory.CHECKSUM_HASH,
                fn=lambda: compute_sha256_integrity(payload.get("data", "")),
                requested_llm=bool(payload.get("requested_llm", False)),
            )
        elif op == "validate_data":
            res, tel, dec = self.execution_router.execute_local_task(
                agent_name="local-engine",
                task_id=f"validate:{uuid.uuid4()}",
                task_category=TaskCategory.SCHEMA_VALIDATION,
                fn=lambda: validate_and_parse_structured_data(
                    str(payload.get("raw", "")),
                    fmt=payload.get("format", "auto"),
                    required_fields=payload.get("required_fields"),
                ),
            )
        elif op == "csv_analysis":
            res, tel, dec = self.execution_router.execute_local_task(
                agent_name="data-analyst",
                task_id=f"csv:{uuid.uuid4()}",
                task_category=TaskCategory.CSV_STATISTICAL_ANALYSIS,
                fn=lambda: analyze_tabular_csv_statistics(str(payload.get("csv", ""))),
                requested_llm=bool(payload.get("requested_llm", False)),
            )
        elif op == "ohlcv_volatility":
            res, tel, dec = self.execution_router.execute_local_task(
                agent_name="data-analyst",
                task_id=f"vol:{uuid.uuid4()}",
                task_category=TaskCategory.OHLCV_VOLATILITY,
                fn=lambda: analyze_ohlcv_and_volatility(
                    list(payload.get("ohlcv") or []),
                    window=int(payload.get("window", 30)),
                ),
                requested_llm=bool(payload.get("requested_llm", False)),
            )
        elif op == "monte_carlo":
            res, tel, dec = self.execution_router.execute_local_task(
                agent_name="data-analyst",
                task_id=f"mc:{uuid.uuid4()}",
                task_category=TaskCategory.MONTE_CARLO_SIMULATION,
                fn=lambda: run_monte_carlo_simulation(
                    spot_price=float(payload["spot_price"]),
                    daily_volatility=float(payload["daily_volatility"]),
                    daily_drift=float(payload.get("daily_drift", 0.0)),
                    horizon_days=int(payload.get("horizon_days", 30)),
                    num_paths=int(payload.get("num_paths", 10_000)),
                    seed=int(payload.get("seed", 42)),
                    historical_log_returns=payload.get("historical_log_returns"),
                ),
                requested_llm=bool(payload.get("requested_llm", False)),
            )
        elif op == "btc_price":
            return self.fetch_btc_market_data_controlled(
                user_id=user_id,
                organization_id=organization_id,
                ttl_seconds=int(payload.get("ttl_seconds", 300)),
                max_age_seconds=payload.get("max_age_seconds"),
                market_api_url=payload.get("market_api_url"),
                allow_stale_on_error=bool(payload.get("allow_stale_on_error", True)),
            )
        else:
            raise ValueError(f"Unsupported local-first operation '{operation}'.")
        return {
            "result": res,
            "telemetry": tel.to_dict(),
            "routing_decision": dec.to_dict(),
        }

    def create_claim_and_evidence(
        self,
        *,
        user_id: str,
        organization_id: str,
        research_project_id: str,
        statement: str,
        claim_type: str = "empirical",
        evidence_links: list[dict[str, Any]] | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="research:write",
        )
        self._require_org_entity(
            self.projects, research_project_id, organization_id, "ResearchProject"
        )
        cid = str(uuid.uuid4())
        now = utcnow().isoformat()
        created_ev: list[dict[str, Any]] = []
        for ev in evidence_links or []:
            src = self._require_org_entity(
                self.sources, ev["source_id"], organization_id, "Source"
            )
            ev_rec = {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "claim_id": cid,
                "source_id": src["id"],
                "source_location": ev.get("source_location", "section:1"),
                "supporting_text": ev["supporting_text"],
                "evidence_type": ev.get("evidence_type", "supporting"),
                "relevance": float(ev.get("relevance", 0.85)),
                "verification_status": "verified",
                "created_at": now,
            }
            self.evidence[ev_rec["id"]] = ev_rec
            created_ev.append(ev_rec)

        status, assessment = evaluate_claim_evidence_status(created_ev)
        claim = {
            "id": cid,
            "organization_id": organization_id,
            "research_project_id": research_project_id,
            "statement": statement,
            "claim_type": claim_type,
            "status": status,
            "confidence_assessment": assessment,
            "created_by": user_id,
            "workspace_scope": workspace_scope,
            "created_at": now,
        }
        self.claims[cid] = claim
        return {**claim, "evidence": created_ev}

    def update_claim_status(
        self,
        *,
        user_id: str,
        organization_id: str,
        claim_id: str,
        status: str,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="research:write",
        )
        if status not in VALID_CLAIM_STATUSES:
            raise ValueError(f"Unsupported claim status '{status}'.")
        claim = self._require_org_entity(self.claims, claim_id, organization_id, "Claim")
        claim["status"] = status
        return claim

    def get_evidence_graph(
        self,
        *,
        user_id: str,
        organization_id: str,
        project_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        claims = [
            c
            for c in self.claims.values()
            if c["organization_id"] == organization_id
            and (project_id is None or c["research_project_id"] == project_id)
            and (scope in {None, "all"} or c["workspace_scope"] == scope)
        ]
        claim_ids = {c["id"] for c in claims}
        ev_list = [
            e
            for e in self.evidence.values()
            if e["organization_id"] == organization_id and e["claim_id"] in claim_ids
        ]
        sources = [
            s for s in self.sources.values() if s["organization_id"] == organization_id
        ]
        return build_evidence_graph_topology(
            claims=claims, evidence_list=ev_list, sources=sources
        )

    # =========================================================================
    # SHARED MEMORY (7 TYPES, CHUNKING, EMBEDDINGS, HYBRID SEARCH & RETENTION)
    # =========================================================================

    def create_memory_object(
        self,
        *,
        user_id: str,
        organization_id: str,
        memory_type: str,
        title: str,
        content: str,
        source_ref: str,
        project_id: str | None = None,
        retention_days: int = 365,
        metadata: dict[str, Any] | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="memory:write",
        )
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"Unsupported memory_type '{memory_type}'.")
        mid = str(uuid.uuid4())
        now = utcnow().isoformat()
        mem = {
            "id": mid,
            "organization_id": organization_id,
            "owner_id": user_id,
            "project_id": project_id,
            "memory_type": memory_type,
            "title": title,
            "content": content,
            "source_ref": source_ref,
            "access_policy": {"visibility": "organization", "rls_enforced": True},
            "version": 1,
            "retention_state": "active",
            "retention_days": retention_days,
            "metadata": metadata or {},
            "workspace_scope": workspace_scope,
            "created_at": now,
            "updated_at": now,
        }
        self.memories[mid] = mem
        self._index_memory_chunks(mem)
        return {**mem, "chunk_count": len(self.memory_chunks.get(mid, []))}

    def search_memories(
        self,
        *,
        user_id: str,
        organization_id: str,
        query: str,
        memory_type: str | None = None,
        project_id: str | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        candidates: list[dict[str, Any]] = []
        for mid, mem in self.memories.items():
            if mem["organization_id"] != organization_id:
                continue
            if mem["retention_state"] != "active":
                continue
            if memory_type and mem["memory_type"] != memory_type:
                continue
            if project_id and mem.get("project_id") != project_id:
                continue
            for ch in self.memory_chunks.get(mid, []):
                candidates.append(
                    {
                        **ch,
                        "memory_type": mem["memory_type"],
                        "memory_title": mem["title"],
                    }
                )
        return rank_memory_chunks(query, candidates, top_k=top_k)

    def list_memories(
        self, *, user_id: str, organization_id: str, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        out: list[dict[str, Any]] = []
        for mid, m in self.memories.items():
            if m["organization_id"] != organization_id:
                continue
            if m["retention_state"] == "deleted":
                continue
            if memory_type and m["memory_type"] != memory_type:
                continue
            out.append({**m, "chunk_count": len(self.memory_chunks.get(mid, []))})
        return sorted(out, key=lambda x: x["created_at"], reverse=True)

    def reindex_or_delete_memory(
        self,
        *,
        user_id: str,
        organization_id: str,
        memory_id: str,
        action: str,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="memory:write",
        )
        mem = self._require_org_entity(self.memories, memory_id, organization_id, "Memory")
        if action == "delete":
            mem["retention_state"] = "deleted"
            self.memory_chunks.pop(memory_id, None)
            return {"id": memory_id, "status": "deleted"}
        if action == "reindex":
            mem["version"] = int(mem["version"]) + 1
            mem["updated_at"] = utcnow().isoformat()
            self._index_memory_chunks(mem)
            return {
                "id": memory_id,
                "status": "reindexed",
                "version": mem["version"],
                "chunk_count": len(self.memory_chunks.get(memory_id, [])),
            }
        raise ValueError(f"Unsupported memory action '{action}'.")

    # =========================================================================
    # DATASETS, EXPERIMENTS, REPRODUCTION & EVALUATIONS
    # =========================================================================

    def create_dataset(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        description: str,
        category: str,
        records: list[dict[str, Any]],
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="experiments:write",
        )
        did = str(uuid.uuid4())
        now = utcnow().isoformat()
        checksum = compute_dataset_checksum(records)
        ds = {
            "id": did,
            "organization_id": organization_id,
            "name": name,
            "description": description,
            "category": category,
            "current_version": 1,
            "row_count": len(records),
            "checksum_sha256": checksum,
            "workspace_scope": workspace_scope,
            "created_at": now,
        }
        self.datasets[did] = ds
        self.dataset_versions[did] = [
            {
                "id": str(uuid.uuid4()),
                "dataset_id": did,
                "version": 1,
                "checksum_sha256": checksum,
                "row_count": len(records),
                "records_json": records,
                "created_at": now,
            }
        ]
        return ds

    def add_dataset_version(
        self,
        *,
        user_id: str,
        organization_id: str,
        dataset_id: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="experiments:write",
        )
        ds = self._require_org_entity(self.datasets, dataset_id, organization_id, "Dataset")
        next_v = int(ds["current_version"]) + 1
        checksum = compute_dataset_checksum(records)
        ver_obj = {
            "id": str(uuid.uuid4()),
            "dataset_id": dataset_id,
            "version": next_v,
            "checksum_sha256": checksum,
            "row_count": len(records),
            "records_json": records,
            "created_at": utcnow().isoformat(),
        }
        self.dataset_versions.setdefault(dataset_id, []).append(ver_obj)
        ds["current_version"] = next_v
        ds["row_count"] = len(records)
        ds["checksum_sha256"] = checksum
        return ver_obj

    def list_datasets(self, *, user_id: str, organization_id: str) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        out = []
        for did, ds in self.datasets.items():
            if ds["organization_id"] == organization_id:
                out.append({**ds, "versions": self.dataset_versions.get(did, [])})
        return out

    def create_and_run_experiment(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        description: str,
        dataset_id: str,
        dataset_version: int = 1,
        execution_mode: str = "multi_agent_team",
        agent_id: str | None = None,
        team_id: str | None = None,
        model_identifier: str = "apex-analytical-local-v1",
        parameters: dict[str, Any] | None = None,
        research_project_id: str | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="experiments:run",
        )
        self._enforce_operational_guards(organization_id)
        ds = self._require_org_entity(self.datasets, dataset_id, organization_id, "Dataset")
        ver_obj = next(
            (
                v
                for v in self.dataset_versions.get(dataset_id, [])
                if int(v["version"]) == int(dataset_version)
            ),
            None,
        )
        if ver_obj is None:
            raise ResourceNotFoundError("Dataset version not found.")

        eid = str(uuid.uuid4())
        now = utcnow().isoformat()
        exp = {
            "id": eid,
            "organization_id": organization_id,
            "research_project_id": research_project_id,
            "name": name,
            "description": description,
            "dataset_id": ds["id"],
            "dataset_version": int(dataset_version),
            "execution_mode": execution_mode,
            "agent_id": agent_id,
            "agent_version": self.agents[agent_id]["current_version"]
            if agent_id and agent_id in self.agents
            else None,
            "team_id": team_id,
            "model_identifier": model_identifier,
            "model_config_json": {"temperature": 0.1},
            "parameters_json": parameters or {},
            "execution_config_json": {
                "non_root": True,
                "network_restricted": True,
                "secrets_mounted": False,
                "timeout_seconds": 60,
            },
            "status": "COMPLETED",
            "workspace_scope": workspace_scope,
            "created_at": now,
        }
        self.experiments[eid] = exp

        agent_run = self.launch_agent_run(
            user_id=user_id,
            organization_id=organization_id,
            objective=f"Eksperyment: {name}",
            execution_mode=execution_mode,
            research_project_id=research_project_id,
            agent_id=agent_id,
            team_id=team_id,
            workspace_scope=workspace_scope,
        )
        exp_run = execute_controlled_experiment_protocol(
            experiment=exp,
            dataset_version_obj=ver_obj,
            agent_run_result=agent_run,
        )
        self.experiment_runs[exp_run["id"]] = exp_run
        return {"experiment": exp, "run": exp_run, "agent_run_id": agent_run["id"]}

    def reproduce_experiment_run(
        self, *, user_id: str, organization_id: str, experiment_run_id: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="experiments:run",
        )
        orig_run = self._require_org_entity(
            self.experiment_runs, experiment_run_id, organization_id, "ExperimentRun"
        )
        exp = self._require_org_entity(
            self.experiments, orig_run["experiment_id"], organization_id, "Experiment"
        )
        ver_obj = next(
            v
            for v in self.dataset_versions[exp["dataset_id"]]
            if int(v["version"]) == int(exp["dataset_version"])
        )
        agent_run = self.launch_agent_run(
            user_id=user_id,
            organization_id=organization_id,
            objective=f"Reprodukcja: {exp['name']}",
            execution_mode=exp["execution_mode"],
            research_project_id=exp.get("research_project_id"),
            agent_id=exp.get("agent_id"),
            team_id=exp.get("team_id"),
            workspace_scope=exp["workspace_scope"],
        )
        repro_run = execute_controlled_experiment_protocol(
            experiment=exp,
            dataset_version_obj=ver_obj,
            agent_run_result=agent_run,
            reproduced_from_run_id=orig_run["id"],
        )
        # Verify deterministic metric parity
        orig_metrics = {
            m["metric_name"]: m["metric_value"]
            for m in orig_run["metrics"]
            if m["metric_name"] in {"claim_recall", "contradiction_recall", "evidence_attribution_rate"}
        }
        new_metrics = {
            m["metric_name"]: m["metric_value"]
            for m in repro_run["metrics"]
            if m["metric_name"] in orig_metrics
        }
        repro_run["reproduction_verified"] = orig_metrics == new_metrics
        self.experiment_runs[repro_run["id"]] = repro_run
        return repro_run

    def list_experiments(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        exps = [
            e
            for e in self.experiments.values()
            if e["organization_id"] == organization_id
            and (scope in {None, "all"} or e["workspace_scope"] == scope)
        ]
        out = []
        for e in exps:
            runs = [
                r
                for r in self.experiment_runs.values()
                if r["experiment_id"] == e["id"]
            ]
            out.append({**e, "runs": runs})
        return out

    def run_evaluation_benchmark(
        self,
        *,
        user_id: str,
        organization_id: str,
        suite_id: str,
        label: str,
        execution_mode: str,
        agent_id: str | None = None,
        team_id: str | None = None,
        model_identifier: str = "apex-analytical-local-v1",
        baseline_eval_run_id: str | None = None,
        workspace_scope: str = "research",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="evaluations:write",
        )
        suite = self._require_org_entity(
            self.eval_suites, suite_id, organization_id, "EvaluationSuite"
        )
        cases = self.eval_cases.get(suite_id, [])
        exp_bundle = self.create_and_run_experiment(
            user_id=user_id,
            organization_id=organization_id,
            name=f"Eval Run: {label}",
            description=f"Controlled benchmark on suite {suite['name']}",
            dataset_id=suite["dataset_id"],
            dataset_version=suite["dataset_version"],
            execution_mode=execution_mode,
            agent_id=agent_id,
            team_id=team_id,
            model_identifier=model_identifier,
            workspace_scope=workspace_scope,
        )
        agent_run = self.agent_runs[exp_bundle["agent_run_id"]]
        exp_run = exp_bundle["run"]

        base_scores = None
        if baseline_eval_run_id and baseline_eval_run_id in self.eval_runs:
            base_scores = self.eval_runs[baseline_eval_run_id]["summary_scores"]

        summary_scores, score_rows, regs = score_evaluation_run(
            suite=suite,
            cases=cases,
            agent_run_result=agent_run,
            experiment_run_result=exp_run,
            baseline_summary_scores=base_scores,
        )
        eval_id = str(uuid.uuid4())
        now = utcnow().isoformat()
        eval_rec = {
            "id": eval_id,
            "organization_id": organization_id,
            "suite_id": suite_id,
            "suite_name": suite["name"],
            "label": label,
            "subject_type": execution_mode,
            "agent_id": agent_id,
            "team_id": team_id,
            "model_identifier": model_identifier,
            "experiment_run_id": exp_run["id"],
            "agent_run_id": agent_run["id"],
            "tokens_used": agent_run["tokens_used"],
            "cost_usd": agent_run["cost_usd"],
            "duration_ms": exp_run["duration_ms"],
            "summary_scores": summary_scores,
            "status": "COMPLETED",
            "workspace_scope": workspace_scope,
            "created_at": now,
        }
        self.eval_runs[eval_id] = eval_rec
        self.eval_scores[eval_id] = score_rows
        for r in regs:
            self.regressions.append(
                {
                    "id": str(uuid.uuid4()),
                    "organization_id": organization_id,
                    "baseline_run_id": baseline_eval_run_id,
                    "candidate_run_id": eval_id,
                    **r,
                    "detected_at": now,
                }
            )
        return {**eval_rec, "scores": score_rows, "regressions": regs}

    def submit_human_evaluation_score(
        self,
        *,
        user_id: str,
        organization_id: str,
        evaluation_run_id: str,
        category: str,
        score: float,
        rationale: str,
    ) -> dict[str, Any]:
        """Record manual human evaluator score without auto-updating production agents."""
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="evaluations:human_review",
        )
        self._require_org_entity(
            self.eval_runs, evaluation_run_id, organization_id, "EvaluationRun"
        )
        if not (0.0 <= score <= 1.0):
            raise ValueError("Score must be between 0.0 and 1.0.")
        row = {
            "id": str(uuid.uuid4()),
            "category": category,
            "score": round(score, 4),
            "rationale": rationale,
            "failure_category": None,
            "evaluated_by": user_id,
            "is_human_review": True,
            "production_agent_auto_updated": False,
            "created_at": utcnow().isoformat(),
        }
        self.eval_scores.setdefault(evaluation_run_id, []).append(row)
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="evaluation.human_review_recorded",
            resource_type="evaluation_run",
            resource_id=evaluation_run_id,
            risk_level="R1",
            details={"category": category, "score": score, "auto_promote": False},
        )
        return row

    def list_evaluations(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        suites = [
            {**s, "cases": self.eval_cases.get(s["id"], [])}
            for s in self.eval_suites.values()
            if s["organization_id"] == organization_id
        ]
        runs = [
            {**r, "scores": self.eval_scores.get(r["id"], [])}
            for r in self.eval_runs.values()
            if r["organization_id"] == organization_id
            and (scope in {None, "all"} or r["workspace_scope"] == scope)
        ]
        regs = [
            rg for rg in self.regressions if rg["organization_id"] == organization_id
        ]
        return {"suites": suites, "runs": runs, "regressions": regs}

    # =========================================================================
    # MODELS, HYBRID LOCAL NODES, WORKFLOWS, GOVERNANCE, ANALYTICS & PLUGINS
    # =========================================================================

    def _ensure_lmstudio_lan_provider(self, organization_id: str) -> list[dict[str, Any]]:
        providers = self.model_providers.setdefault(organization_id, [])
        if not any(p.get("slug") == "lmstudio-lan-qwen" for p in providers):
            providers.insert(
                1 if providers else 0,
                {
                    "id": str(uuid.uuid4()),
                    "organization_id": organization_id,
                    "slug": "lmstudio-lan-qwen",
                    "name": "LM Studio LAN (192.168.18.184:1234 · Qwen 3.6 35B-A3B)",
                    "category": "compatible_http",
                    "base_url": "http://192.168.18.184:1234/v1",
                    "auth_env_var": None,
                    "fallback_provider_slug": "apex-analytical",
                    "rate_limit_rpm": 300,
                    "budget_limit_usd": 0.0,
                    "models": [
                        {
                            "id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                            "capabilities": [
                                "reasoning",
                                "structured_output",
                                "tools",
                                "local_lan",
                            ],
                            "cost_per_1k_input_usd": 0.0,
                            "cost_per_1k_output_usd": 0.0,
                        }
                    ],
                    "enabled": True,
                },
            )
        return providers

    def list_model_providers(
        self, *, user_id: str, organization_id: str
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        providers = self._ensure_lmstudio_lan_provider(organization_id)
        bridge_online = self.llm_gateway.lan_bridge.is_online(organization_id)
        enriched: list[dict[str, Any]] = []
        for p in providers:
            configured = self.llm_gateway.has_credentials(organization_id, p)
            status = (
                "READY_LOCAL_DETERMINISTIC"
                if p["slug"] == "apex-analytical"
                else ("CONFIGURED" if configured else "UNCONFIGURED_MISSING_KEY")
            )
            if p["slug"] == "lmstudio-lan-qwen":
                status = "BRIDGE_ONLINE_LAN" if bridge_online else "CONFIGURED_LAN_ENDPOINT"
            elif p["category"] == "local_ollama":
                if bridge_online:
                    status = "BRIDGE_ONLINE_LAN"
                elif not configured:
                    status = "OFFLINE_IN_ARENA_SANDBOX"
            enriched.append({**p, "credentials_present": configured, "health_status": status})
        return enriched

    def configure_model_provider_secret(
        self,
        *,
        user_id: str,
        organization_id: str,
        provider_slug: str,
        api_key: str,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="models:manage",
        )
        providers = self._ensure_lmstudio_lan_provider(organization_id)
        alias_map = {
            "openai": "openai-compatible-cloud",
            "anthropic": "anthropic-cloud",
            "ollama": "ollama-local",
            "lmstudio": "lmstudio-lan-qwen",
        }
        resolved_slug = alias_map.get(provider_slug, provider_slug)
        target = next(
            (p for p in providers if p["slug"] in {provider_slug, resolved_slug}),
            None,
        )
        if target is None:
            raise ResourceNotFoundError(f"Model provider '{provider_slug}' not found.")
        canonical_slug = str(target["slug"])
        if base_url:
            target["base_url"] = base_url.strip()
        meta = self.llm_gateway.set_runtime_secret(
            organization_id=organization_id,
            provider_slug=canonical_slug,
            api_key=api_key,
        )
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="models.configure_secret",
            resource_type="model_provider",
            resource_id=target["id"],
            risk_level="R2",
            details={"provider_slug": canonical_slug, "configured": meta["configured"]},
        )
        return {
            "provider_slug": canonical_slug,
            "credentials_present": self.llm_gateway.has_credentials(organization_id, target),
            "health_status": "CONFIGURED"
            if self.llm_gateway.has_credentials(organization_id, target)
            else "UNCONFIGURED_MISSING_KEY",
        }

    def test_model_provider_inference(
        self,
        *,
        user_id: str,
        organization_id: str,
        model_identifier: str,
        prompt: str = "Summarize empirical verification principles in two sentences.",
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="models:manage",
        )
        providers = self._ensure_lmstudio_lan_provider(organization_id)
        res = self.llm_gateway.complete_sync(
            organization_id=organization_id,
            providers_catalog=providers,
            preferred_model=model_identifier,
            system_prompt="You are APEX ONE verification diagnostics.",
            user_prompt=prompt,
            bypass_circuit_breaker=True,
        )
        if res is None:
            return {
                "live_llm_invoked": False,
                "provider_slug": "apex-analytical",
                "model_identifier": "apex-analytical-local-v1",
                "content": (
                    "Deterministic local analytical fallback active — configure an API key "
                    "or LAN/Ollama endpoint to enable external live inference."
                ),
                "total_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": 1,
                "fallback_triggered": True,
                "error_note": None,
            }
        content_msg = res.content
        if not content_msg and res.error_note:
            content_msg = (
                f"Nie udało się połączyć z endpointem docelowym ({res.error_note}) — "
                "aktywowano automatyczny fallback do silnika analitycznego. "
                "Jeśli endpoint działa w prywatnej sieci LAN (np. 192.168.18.184:1234), "
                "uruchom backend APEX ONE lub węzeł Hybrid Local Node w tej samej sieci LAN."
            )
        return {
            "live_llm_invoked": res.live_llm_invoked,
            "provider_slug": res.provider_slug,
            "model_identifier": res.model_identifier,
            "content": content_msg,
            "total_tokens": res.total_tokens,
            "cost_usd": res.cost_usd,
            "latency_ms": res.latency_ms,
            "fallback_triggered": res.fallback_triggered,
            "error_note": res.error_note,
        }

    def register_model_provider(
        self, *, user_id: str, organization_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="models:manage",
        )
        rec = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "slug": payload["slug"],
            "name": payload["name"],
            "category": payload["category"],
            "base_url": payload.get("base_url", ""),
            "auth_env_var": payload.get("auth_env_var"),
            "fallback_provider_slug": payload.get("fallback_provider_slug"),
            "rate_limit_rpm": int(payload.get("rate_limit_rpm", 60)),
            "budget_limit_usd": float(payload.get("budget_limit_usd", 50.0)),
            "models": payload.get("models", []),
            "enabled": True,
            "created_at": utcnow().isoformat(),
        }
        self.model_providers.setdefault(organization_id, []).append(rec)
        return rec

    def register_local_node_device(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        allowed_directories: list[str],
        can_read: bool = True,
        can_write: bool = False,
        can_execute: bool = False,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="nodes:manage",
        )
        raw_tok, tok_hash = generate_secure_token("apxnode")
        did = str(uuid.uuid4())
        node_id = f"lnode-{uuid.uuid4().hex[:10]}"
        expires = (utcnow() + timedelta(hours=24)).isoformat()
        dev = {
            "id": did,
            "organization_id": organization_id,
            "registered_by": user_id,
            "node_id": node_id,
            "name": name,
            "allowed_directories": allowed_directories,
            "capability_grants": {
                "read": can_read,
                "write": can_write,
                "execute": can_execute,
                "unrestricted_terminal": False,
            },
            "pairing_token_hash": tok_hash,
            "token_expires_at": expires,
            "status": "PAIRED_AWAITING_CONNECTION",
            "user_machine_connected": False,
            "last_heartbeat_at": None,
            "revoked_at": None,
            "created_at": utcnow().isoformat(),
        }
        self.devices[did] = dev
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="hybrid_node.register_device",
            resource_type="device",
            resource_id=did,
            risk_level="R2",
            details={"node_id": node_id, "grants": dev["capability_grants"]},
        )
        return {
            **{k: v for k, v in dev.items() if k != "pairing_token_hash"},
            "pairing_token_once": raw_tok,
        }

    def revoke_local_node_device(
        self, *, user_id: str, organization_id: str, device_id: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="nodes:manage",
        )
        dev = self._require_org_entity(self.devices, device_id, organization_id, "Device")
        dev["status"] = "REVOKED"
        dev["revoked_at"] = utcnow().isoformat()
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="hybrid_node.revoke_device",
            resource_type="device",
            resource_id=device_id,
            risk_level="R3",
        )
        return {k: v for k, v in dev.items() if k != "pairing_token_hash"}

    def list_local_node_devices(
        self, *, user_id: str, organization_id: str
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        return [
            {k: v for k, v in d.items() if k != "pairing_token_hash"}
            for d in self.devices.values()
            if d["organization_id"] == organization_id
        ]

    # =========================================================================
    # GOVERNANCE: R0-R3 APPROVALS, PARAMETER BINDING & EMERGENCY STOP
    # =========================================================================

    def create_approval_request(
        self,
        *,
        user_id: str,
        organization_id: str,
        operation_type: str,
        risk_level: RiskLevel,
        parameters: dict[str, Any],
        execution_version: int = 1,
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        param_hash = compute_approval_parameter_hash(
            organization_id=organization_id,
            operation_type=operation_type,
            execution_version=execution_version,
            parameters=parameters,
        )
        aid = str(uuid.uuid4())
        now = utcnow().isoformat()
        rec = {
            "id": aid,
            "organization_id": organization_id,
            "operation_type": operation_type,
            "risk_level": risk_level,
            "parameters_json": parameters,
            "parameters_sha256": param_hash,
            "requested_by": user_id,
            "decided_by": None,
            "execution_version": execution_version,
            "status": "PENDING",
            "decision_note": "",
            "expires_at": default_approval_expiry(24).isoformat(),
            "decided_at": None,
            "created_at": now,
        }
        self.approvals[aid] = rec
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="governance.request_approval",
            resource_type="approval",
            resource_id=aid,
            risk_level=risk_level,
            details={"operation_type": operation_type, "parameters_sha256": param_hash},
        )
        return rec

    def decide_approval(
        self,
        *,
        user_id: str,
        organization_id: str,
        approval_id: str,
        decision: str,
        decision_note: str = "",
    ) -> dict[str, Any]:
        rec = self._require_org_entity(
            self.approvals, approval_id, organization_id, "Approval"
        )
        perm = (
            "governance:approve_r3"
            if rec["risk_level"] == "R3"
            else "governance:approve_r2"
        )
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission=perm,
        )
        if decision not in {"APPROVED", "REJECTED"}:
            raise ValueError("Decision must be APPROVED or REJECTED.")
        if datetime.fromisoformat(rec["expires_at"]) <= utcnow():
            rec["status"] = "EXPIRED"
            raise GovernanceBlockedError("Approval request has expired.")

        rec["status"] = decision
        rec["decided_by"] = user_id
        rec["decision_note"] = decision_note
        rec["decided_at"] = utcnow().isoformat()
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action=f"governance.approval.{decision.lower()}",
            resource_type="approval",
            resource_id=approval_id,
            risk_level=rec["risk_level"],
            details={"note": decision_note},
        )
        return rec

    def verify_approval_for_execution(
        self,
        *,
        organization_id: str,
        approval_id: str,
        operation_type: str,
        execution_version: int,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify approval validity; invalidate immediately if parameters changed."""
        rec = self._require_org_entity(
            self.approvals, approval_id, organization_id, "Approval"
        )
        if rec["status"] != "APPROVED":
            raise GovernanceBlockedError(
                f"Approval {approval_id} is not APPROVED (status={rec['status']})."
            )
        if datetime.fromisoformat(rec["expires_at"]) <= utcnow():
            rec["status"] = "EXPIRED"
            raise GovernanceBlockedError("Approval has expired.")

        actual_hash = compute_approval_parameter_hash(
            organization_id=organization_id,
            operation_type=operation_type,
            execution_version=execution_version,
            parameters=parameters,
        )
        if actual_hash != rec["parameters_sha256"]:
            rec["status"] = "INVALIDATED"
            raise GovernanceBlockedError(
                "Approval invalidated: execution parameters or version changed after approval."
            )
        return rec

    def set_emergency_stop(
        self, *, user_id: str, organization_id: str, active: bool, reason: str = ""
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="governance:emergency_stop",
        )
        org = self.organizations[organization_id]
        org["emergency_stop_active"] = active
        if active:
            # Immediately cancel any active runs in this organization
            for r in self.agent_runs.values():
                if r["organization_id"] == organization_id and r["status"] in {"PENDING", "RUNNING"}:
                    r["status"] = "CANCELLED"
                    r["completed_at"] = utcnow().isoformat()
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="governance.emergency_stop",
            resource_type="organization",
            resource_id=organization_id,
            risk_level="R3",
            details={"active": active, "reason": reason},
        )
        return org

    def list_governance_state(
        self, *, user_id: str, organization_id: str
    ) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        org = self.organizations[organization_id]
        approvals = [
            a for a in self.approvals.values() if a["organization_id"] == organization_id
        ]
        audit_entries = [asdict(e) for e in self.audit.list_entries(organization_id)]
        chain_check = self.audit.verify_chain(organization_id)
        budgets = self.budgets.get(organization_id, [])
        return {
            "emergency_stop_active": org["emergency_stop_active"],
            "policies": org["policies"],
            "approvals": sorted(approvals, key=lambda x: x["created_at"], reverse=True),
            "audit_events": audit_entries,
            "audit_chain_integrity": chain_check,
            "budgets": budgets,
        }

    # =========================================================================
    # WORKFLOWS, ANALYTICS & EXTENSIONS
    # =========================================================================

    def create_and_trigger_workflow(
        self,
        *,
        user_id: str,
        organization_id: str,
        name: str,
        description: str,
        risk_level: RiskLevel = "R1",
        require_approval: bool = False,
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="workflows:write",
        )
        self._enforce_operational_guards(organization_id)
        wid = str(uuid.uuid4())
        now = utcnow().isoformat()
        wf_def = {
            "id": wid,
            "organization_id": organization_id,
            "name": name,
            "description": description,
            "risk_level": risk_level,
            "steps": [
                {"key": "ingest_sources", "title": "Weryfikacja źródeł"},
                {"key": "extract_evidence", "title": "Analiza dowodów"},
                {"key": "publish_summary", "title": "Zapis raportu"},
            ],
            "created_at": now,
        }
        self.workflow_defs[wid] = wf_def

        approval_id = None
        run_status = "COMPLETED"
        if require_approval or risk_level in {"R2", "R3"}:
            app_rec = self.create_approval_request(
                user_id=user_id,
                organization_id=organization_id,
                operation_type=f"workflow.execute:{wid}",
                risk_level=risk_level,
                parameters={"workflow_id": wid, "name": name},
            )
            approval_id = app_rec["id"]
            run_status = "AWAITING_APPROVAL"

        wr_id = str(uuid.uuid4())
        wf_run = {
            "id": wr_id,
            "organization_id": organization_id,
            "workflow_id": wid,
            "workflow_name": name,
            "risk_level": risk_level,
            "approval_id": approval_id,
            "status": run_status,
            "idempotency_key": f"wfrun:{wid}:{wr_id[:8]}",
            "created_at": now,
            "updated_at": now,
        }
        self.workflow_runs_store[wr_id] = wf_run
        return {"definition": wf_def, "run": wf_run}

    def resume_approved_workflow_run(
        self, *, user_id: str, organization_id: str, workflow_run_id: str
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="workflows:write",
        )
        self._enforce_operational_guards(organization_id)
        wfr = self._require_org_entity(
            self.workflow_runs_store, workflow_run_id, organization_id, "WorkflowRun"
        )
        if wfr.get("approval_id"):
            self.verify_approval_for_execution(
                organization_id=organization_id,
                approval_id=wfr["approval_id"],
                operation_type=f"workflow.execute:{wfr['workflow_id']}",
                execution_version=1,
                parameters={"workflow_id": wfr["workflow_id"], "name": wfr["workflow_name"]},
            )
        wfr["status"] = "COMPLETED"
        wfr["updated_at"] = utcnow().isoformat()
        return wfr

    def list_workflows(self, *, user_id: str, organization_id: str) -> dict[str, Any]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        defs = [
            d
            for d in self.workflow_defs.values()
            if d["organization_id"] == organization_id
        ]
        runs = [
            r
            for r in self.workflow_runs_store.values()
            if r["organization_id"] == organization_id
        ]
        return {"definitions": defs, "runs": runs}

    def get_analytics_summary(
        self,
        *,
        user_id: str,
        organization_id: str,
        scope: str | None = None,
        since_iso: str | None = None,
    ) -> dict[str, Any]:
        """Compute 100% genuine metrics from actual recorded runs, usage, and evaluations."""
        self.authorize(user_id=user_id, organization_id=organization_id)
        runs = [
            r
            for r in self.agent_runs.values()
            if r["organization_id"] == organization_id
            and (scope in {None, "all"} or r["workspace_scope"] == scope)
            and (not since_iso or r["created_at"] >= since_iso)
        ]
        usages = [
            u
            for u in self.usage_records
            if u["organization_id"] == organization_id
            and (scope in {None, "all"} or u["workspace_scope"] == scope)
            and (not since_iso or u["recorded_at"] >= since_iso)
        ]
        evals = [
            ev
            for ev in self.eval_runs.values()
            if ev["organization_id"] == organization_id
            and (scope in {None, "all"} or ev["workspace_scope"] == scope)
        ]
        wf_runs = [
            w
            for w in self.workflow_runs_store.values()
            if w["organization_id"] == organization_id
        ]
        devices = [
            d for d in self.devices.values() if d["organization_id"] == organization_id
        ]
        pending_approvals = [
            a
            for a in self.approvals.values()
            if a["organization_id"] == organization_id and a["status"] == "PENDING"
        ]

        total_runs = len(runs)
        succeeded = sum(1 for r in runs if r["status"] == "COMPLETED")
        failed = sum(1 for r in runs if r["status"] == "FAILED")
        avg_ms = (
            round(sum(int(r["duration_ms"]) for r in runs) / float(total_runs), 1)
            if total_runs
            else 0.0
        )
        total_cost = round(sum(float(u["cost_usd"]) for u in usages), 6)
        total_tokens = sum(int(u["total_tokens"]) for u in usages)
        total_tools = sum(int(u["tool_calls_count"]) for u in usages)

        org_budget = self.budgets.get(organization_id, [{"max_cost_usd": 100.0}])[0]
        max_budget = float(org_budget.get("max_cost_usd", 100.0))

        return {
            "agent_execution_count": total_runs,
            "successful_executions": succeeded,
            "failure_rate_pct": round(100.0 * failed / max(1, total_runs), 2),
            "avg_execution_time_ms": avg_ms,
            "total_model_cost_usd": total_cost,
            "total_tokens_consumed": total_tokens,
            "tool_invocation_count": total_tools,
            "workflow_total": len(wf_runs),
            "workflow_succeeded": sum(
                1 for w in wf_runs if w["status"] == "COMPLETED"
            ),
            "pending_approvals_count": len(pending_approvals),
            "active_local_nodes_count": sum(
                1 for d in devices if d.get("user_machine_connected") is True
            ),
            "paired_local_nodes_count": sum(
                1 for d in devices if d["status"] != "REVOKED"
            ),
            "budget_limit_usd": max_budget,
            "budget_utilization_pct": round(100.0 * total_cost / max(0.01, max_budget), 3),
            "benchmark_comparisons": [
                {
                    "id": ev["id"],
                    "label": ev["label"],
                    "subject_type": ev["subject_type"],
                    "tokens_used": ev["tokens_used"],
                    "cost_usd": ev["cost_usd"],
                    "duration_ms": ev["duration_ms"],
                    "scores": ev["summary_scores"],
                }
                for ev in evals
            ],
            "usage_series": usages[-30:],
        }

    def export_analytics_csv(
        self, *, user_id: str, organization_id: str, scope: str | None = None
    ) -> str:
        self.authorize(user_id=user_id, organization_id=organization_id)
        usages = [
            u
            for u in self.usage_records
            if u["organization_id"] == organization_id
            and (scope in {None, "all"} or u["workspace_scope"] == scope)
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "recorded_at",
                "agent_run_id",
                "model_identifier",
                "total_tokens",
                "cost_usd",
                "latency_ms",
                "tool_calls_count",
                "workspace_scope",
            ],
        )
        writer.writeheader()
        for u in usages:
            writer.writerow({k: u.get(k) for k in writer.fieldnames})
        return buf.getvalue()

    async def list_extensions_with_health(
        self, *, user_id: str, organization_id: str
    ) -> list[dict[str, Any]]:
        self.authorize(user_id=user_id, organization_id=organization_id)
        exts = self.extensions.get(organization_id, [])
        ws_health = await check_web_search_health()
        ol_health = await check_ollama_health()
        gh_health = await check_github_integration_health()
        live_map = {
            "web-search": asdict(ws_health),
            "ollama": asdict(ol_health),
            "github": asdict(gh_health),
        }
        return [
            {**ext, "live_health": live_map.get(ext["slug"])}
            for ext in exts
        ]

    def toggle_extension(
        self, *, user_id: str, organization_id: str, slug: str, enabled: bool
    ) -> dict[str, Any]:
        self.authorize(
            user_id=user_id,
            organization_id=organization_id,
            required_permission="extensions:manage",
        )
        exts = self.extensions.get(organization_id, [])
        target = next((e for e in exts if e["slug"] == slug), None)
        if not target:
            raise ResourceNotFoundError(f"Extension '{slug}' not found.")
        target["enabled"] = enabled
        self.audit.append(
            organization_id=organization_id,
            actor_id=user_id,
            actor_type="user",
            action="extension.toggle",
            resource_type="extension",
            resource_id=slug,
            risk_level="R2",
            details={"enabled": enabled},
        )
        return target

    # =========================================================================
    # INTERNAL HELPERS & PART IX DEMONSTRATION SEEDING
    # =========================================================================

    def _public_user(self, u: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in u.items() if k != "password_hash"}

    def _require_org_entity(
        self,
        collection: dict[str, dict[str, Any]],
        entity_id: str,
        organization_id: str,
        label: str,
    ) -> dict[str, Any]:
        ent = collection.get(entity_id)
        if ent is None or ent.get("organization_id") != organization_id:
            raise ResourceNotFoundError(f"{label} '{entity_id}' not found in organization.")
        return ent

    def _enforce_operational_guards(self, organization_id: str) -> None:
        org = self.organizations[organization_id]
        if org["emergency_stop_active"]:
            raise GovernanceBlockedError(
                "EMERGENCY STOP is active for this organization; all executions are halted."
            )
        budgets = self.budgets.get(organization_id, [])
        if budgets:
            b0 = budgets[0]
            if b0.get("hard_stop"):
                spent = sum(
                    float(u["cost_usd"])
                    for u in self.usage_records
                    if u["organization_id"] == organization_id
                )
                if spent >= float(b0["max_cost_usd"]):
                    raise GovernanceBlockedError(
                        f"Organizational hard budget limit (${b0['max_cost_usd']:.2f}) exceeded."
                    )

    def _normalize_agent_dna(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "instructions": raw.get("instructions", "Postępuj zgodnie z rygorem dowodowym."),
            "model_policy": raw.get(
                "model_policy",
                {"preferred_category": "analytical", "fallback_chain": ["apex-analytical-local-v1"]},
            ),
            "capabilities": list(raw.get("capabilities", ["research_synthesis"])),
            "allowed_tools": list(raw.get("allowed_tools", ["memory.retrieve"])),
            "input_schema": raw.get("input_schema", {"type": "object"}),
            "output_schema": raw.get("output_schema", {"type": "object"}),
            "memory_policy": raw.get(
                "memory_policy", {"read_types": ["research"], "write_types": ["research"]}
            ),
            "execution_policy": raw.get(
                "execution_policy", {"risk_ceiling": "R1", "retry_limit": 2}
            ),
            "budget": raw.get("budget", {"max_tokens": 24000, "max_cost_usd": 1.5}),
            "max_steps": int(raw.get("max_steps", 8)),
            "timeout": int(raw.get("timeout", 90)),
            "evaluation_profile": raw.get("evaluation_profile", "research_rigor_v1"),
        }

    def _index_memory_chunks(self, mem: dict[str, Any]) -> None:
        raw_chunks = chunk_document_text(mem["content"])
        org_id = mem["organization_id"]
        providers = self.model_providers.get(org_id, [])
        built: list[dict[str, Any]] = []
        allow_ext_emb = os.environ.get("APEX_ENABLE_EXTERNAL_EMBEDDINGS", "").lower() == "true"
        for idx, ch_text in enumerate(raw_chunks):
            vec, backend_name = self.llm_gateway.embed_text_1536_with_live_provider(
                ch_text,
                organization_id=org_id,
                providers_catalog=providers,
                allow_external_cloud=allow_ext_emb,
            )
            built.append(
                {
                    "id": str(uuid.uuid4()),
                    "memory_id": mem["id"],
                    "organization_id": org_id,
                    "chunk_index": idx,
                    "content": ch_text,
                    "embedding": vec,
                    "source_citation": f"{mem['source_ref']}#chunk-{idx + 1}",
                    "metadata": {
                        **(mem.get("metadata") or {}),
                        "embedding_backend": backend_name,
                    },
                }
            )
        self.memory_chunks[mem["id"]] = built

    def _persist_run_side_effects(
        self, run_data: dict[str, Any], *, user_id: str
    ) -> None:
        org_id = run_data["organization_id"]
        if run_data["status"] == "COMPLETED":
            step_tels = [
                (s.get("output_json") or {}).get("telemetry") or {}
                for s in (run_data.get("steps") or [])
            ]
            exact_prompt_tok = sum(int(t.get("input_tokens") or 0) for t in step_tels)
            exact_comp_tok = sum(int(t.get("output_tokens") or 0) for t in step_tels)
            llm_tels = [t for t in step_tels if t.get("llm_invoked")]
            used_prov = str(llm_tels[-1].get("provider")) if llm_tels else "local_python"
            used_mod = str(llm_tels[-1].get("model")) if llm_tels else "none"
            self.usage_records.append(
                {
                    "id": str(uuid.uuid4()),
                    "organization_id": org_id,
                    "agent_run_id": run_data["id"],
                    "model_identifier": used_mod,
                    "provider_name": used_prov,
                    "prompt_tokens": exact_prompt_tok,
                    "completion_tokens": exact_comp_tok,
                    "total_tokens": exact_prompt_tok + exact_comp_tok,
                    "cost_usd": float(run_data["cost_usd"]),
                    "latency_ms": int(run_data["duration_ms"]),
                    "tool_calls_count": len(run_data.get("tool_invocations", [])),
                    "workspace_scope": run_data["workspace_scope"],
                    "recorded_at": utcnow().isoformat(),
                }
            )
            proj_id = run_data.get("research_project_id")
            out = run_data.get("output_json") or {}
            if proj_id and proj_id in self.projects:
                # Persist extracted claims & evidence if not already linked for this run
                created_claim_links: list[dict[str, Any]] = []
                for item in out.get("extracted_claims", []):
                    if item.get("source_id") in self.sources:
                        cl = self.create_claim_and_evidence(
                            user_id=user_id,
                            organization_id=org_id,
                            research_project_id=proj_id,
                            statement=item["statement"],
                            evidence_links=[
                                {
                                    "source_id": item["source_id"],
                                    "source_location": "extracted_span",
                                    "supporting_text": item.get("excerpt", item["statement"]),
                                    "evidence_type": item.get("evidence_type", "supporting"),
                                    "relevance": 0.9,
                                }
                            ],
                            workspace_scope=run_data["workspace_scope"],
                        )
                        created_claim_links.append(
                            {"claim_id": cl["id"], "source_id": item["source_id"]}
                        )
                rep_md = out.get("report_markdown")
                if rep_md:
                    reps = self.reports.setdefault(proj_id, [])
                    reps.append(
                        {
                            "id": str(uuid.uuid4()),
                            "research_project_id": proj_id,
                            "version": len(reps) + 1,
                            "title": f"Raport ({run_data['execution_mode']}): {run_data['objective'][:80]}",
                            "markdown_body": rep_md,
                            "uncertainty_summary": out.get("uncertainty_summary", ""),
                            "claim_links": created_claim_links,
                            "created_at": utcnow().isoformat(),
                        }
                    )

    def _create_organization_internal(
        self, *, name: str, owner_user_id: str
    ) -> dict[str, Any]:
        oid = str(uuid.uuid4())
        now = utcnow().isoformat()
        slug = name.lower().replace(" ", "-")[:40] + "-" + oid[:6]
        org = {
            "id": oid,
            "slug": slug,
            "name": name,
            "policies": {
                "require_r2_approval": True,
                "require_r3_exact_confirmation": True,
                "auto_promote_single_human_eval": False,
            },
            "emergency_stop_active": False,
            "created_at": now,
            "updated_at": now,
        }
        self.organizations[oid] = org
        self.memberships.append(
            {
                "id": str(uuid.uuid4()),
                "organization_id": oid,
                "user_id": owner_user_id,
                "role_name": "Owner",
                "status": "active",
                "joined_at": now,
            }
        )
        return org

    def _provision_org_defaults(
        self, org_id: str, *, actor_id: str, seed_demo: bool
    ) -> None:
        self.budgets[org_id] = [
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "scope_type": "organization",
                "scope_id": org_id,
                "max_cost_usd": 250.0,
                "max_tokens": 10_000_000,
                "period": "monthly",
                "hard_stop": True,
            }
        ]
        self.model_providers[org_id] = [
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "slug": "apex-analytical",
                "name": "APEX Local Deterministic Analytical Engine",
                "category": "compatible_http",
                "base_url": "internal://apex-analytical-local-v1",
                "auth_env_var": None,
                "fallback_provider_slug": None,
                "rate_limit_rpm": 600,
                "budget_limit_usd": 250.0,
                "models": [
                    {"id": "apex-analytical-local-v1", "capabilities": ["reasoning", "structured_output"]},
                    {"id": "apex-embed-v1", "capabilities": ["embeddings_1536"]},
                ],
                "enabled": True,
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "slug": "openai-compatible-cloud",
                "name": "Hosted Commercial LLM Gateway (OpenAI / Compatible HTTP)",
                "category": "commercial_cloud",
                "base_url": "https://api.openai.com/v1",
                "auth_env_var": "OPENAI_API_KEY",
                "fallback_provider_slug": "apex-analytical",
                "rate_limit_rpm": 120,
                "budget_limit_usd": 100.0,
                "models": [
                    {"id": "configurable-frontier-reasoning", "capabilities": ["reasoning", "vision", "tools"]}
                ],
                "enabled": True,
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "slug": "anthropic-cloud",
                "name": "Anthropic Commercial Provider Adapter",
                "category": "commercial_cloud",
                "base_url": "https://api.anthropic.com/v1",
                "auth_env_var": "ANTHROPIC_API_KEY",
                "fallback_provider_slug": "apex-analytical",
                "rate_limit_rpm": 120,
                "budget_limit_usd": 100.0,
                "models": [
                    {"id": "configurable-claude-research", "capabilities": ["reasoning", "vision", "citations"]}
                ],
                "enabled": True,
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "slug": "lmstudio-lan-qwen",
                "name": "LM Studio LAN (192.168.18.184:1234 · Qwen 3.6 35B-A3B)",
                "category": "compatible_http",
                "base_url": "http://192.168.18.184:1234/v1",
                "auth_env_var": None,
                "fallback_provider_slug": "apex-analytical",
                "rate_limit_rpm": 300,
                "budget_limit_usd": 0.0,
                "models": [
                    {
                        "id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                        "capabilities": [
                            "reasoning",
                            "structured_output",
                            "tools",
                            "local_lan",
                        ],
                        "cost_per_1k_input_usd": 0.0,
                        "cost_per_1k_output_usd": 0.0,
                    }
                ],
                "enabled": True,
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "slug": "ollama-local",
                "name": "Ollama Local Model Runtime",
                "category": "local_ollama",
                "base_url": "http://127.0.0.1:11434",
                "auth_env_var": None,
                "fallback_provider_slug": "apex-analytical",
                "rate_limit_rpm": 300,
                "budget_limit_usd": 0.0,
                "models": [
                    {"id": "ollama/configurable-local-llm", "capabilities": ["local_inference", "embeddings"]}
                ],
                "enabled": True,
            },
        ]
        self.extensions[org_id] = create_integration_placeholders()

        # Load all 8 specialized agents + universal baseline from `agents/*/agent_dna.json`
        manifests = load_builtin_agent_dna_manifests(self._repo_root)
        created_agents: dict[str, str] = {}
        for m in manifests:
            ag = self.create_agent(
                user_id=actor_id,
                organization_id=org_id,
                payload={**m, "workspace_scope": "demo" if seed_demo else "research"},
            )
            created_agents[m["slug"]] = ag["id"]

        if created_agents:
            sup_id = created_agents.get("supervisor")
            members = [
                aid
                for slug, aid in created_agents.items()
                if slug not in {"supervisor", "universal-baseline"}
            ]
            self.create_team(
                user_id=actor_id,
                organization_id=org_id,
                name="Zespół Badawczy APEX ONE (8 Ról Specjalistycznych)",
                description="Pełny zespół: Supervisor, Research Planner, Source Scout, Evidence Analyst, Data Analyst, Experiment Engineer, Critic/Replicator, Research Writer.",
                supervisor_agent_id=sup_id,
                member_agent_ids=members,
            )

    def _bootstrap_workspace_and_demo(self) -> None:
        """Seed roles, accounts, datasets, and Part IX comparative demonstration."""
        owner = self.register_user(
            email="admin@apex-one.local",
            password="ApexResearch!2026",
            display_name="Dr Anna Kowalska (Owner)",
            organization_name="APEX ONE Research Laboratory",
            locale="pl",
        )
        org_id = self.list_user_organizations(owner["id"])[0]["organization_id"]
        self.default_org_id = org_id

        # Add Researcher, Evaluator, and Viewer accounts in the same lab organization
        for email, name, role in [
            ("researcher@apex-one.local", "Mgr Piotr Nowak (Researcher)", "Researcher"),
            ("evaluator@apex-one.local", "Dr Ewa Wiśniewska (Evaluator)", "Evaluator"),
            ("viewer@apex-one.local", "Tomasz Zieliński (Viewer)", "Viewer"),
        ]:
            u = self.register_user(email=email, password="ApexResearch!2026", display_name=name)
            self.memberships.append(
                {
                    "id": str(uuid.uuid4()),
                    "organization_id": org_id,
                    "user_id": u["id"],
                    "role_name": role,
                    "status": "active",
                    "joined_at": utcnow().isoformat(),
                }
            )

        # Upgrade seeded agents to exist cleanly in the workspace & run Part IX Demo
        corpus_path = self._repo_root / "datasets" / "synthetic" / "research_evidence_corpus.json"
        bench_path = self._repo_root / "datasets" / "benchmark" / "single_vs_multi_agent_benchmark.json"
        if not corpus_path.exists() or not bench_path.exists():
            return

        corpus_data = json.loads(corpus_path.read_text(encoding="utf-8"))
        bench_data = json.loads(bench_path.read_text(encoding="utf-8"))

        demo_proj = self.create_research_project(
            user_id=owner["id"],
            organization_id=org_id,
            title="Comparative Evaluation of Single-Agent and Multi-Agent Research Systems",
            hypothesis="Wyspecjalizowany zespół wieloagentowy (Configuration B) osiąga wyższe pokrycie dowodowe i wykrywalność sprzeczności niż pojedynczy agent (Configuration A), kosztem wyższego zużycia tokenów i czasu wykonania.",
            scope_summary="[DEMO WORKSPACE] Kontrolowane badanie porównawcze Single-Agent Baseline vs 8-Agent Research Team na oznakowanym korpusie dowodowym SRC-SYN-01..04.",
            workspace_scope="demo",
        )
        pid = demo_proj["id"]
        for st in ["PLANNING", "RESEARCHING", "ANALYZING", "HYPOTHESIS_FORMATION", "EXPERIMENT_DESIGN", "EXECUTING", "VERIFYING", "REPORTING"]:
            self.transition_research_project_state(
                user_id=owner["id"],
                organization_id=org_id,
                project_id=pid,
                target_state=st,
            )

        # Register the 4 labeled synthetic sources into the demo project
        for rec in corpus_data["records"]:
            self.register_source(
                user_id=owner["id"],
                organization_id=org_id,
                source_type=rec["source_type"],
                title=f"[{rec['doc_id']}] {rec['title']}",
                uri_or_filename=f"dataset://synthetic/{rec['doc_id']}.md",
                raw_text=rec["text"],
                research_project_id=pid,
                authors=rec["authors"],
                published_at=rec["published_at"],
                metadata_json={"labeled_claims": rec["labeled_claims"]},
                workspace_scope="demo",
            )

        # Seed Dataset & Evaluation Suite
        ds = self.create_dataset(
            user_id=owner["id"],
            organization_id=org_id,
            name=corpus_data["dataset_name"],
            description=corpus_data["description"],
            category="synthetic",
            records=corpus_data["records"],
            workspace_scope="demo",
        )

        suite_id = str(uuid.uuid4())
        self.eval_suites[suite_id] = {
            "id": suite_id,
            "organization_id": org_id,
            "name": bench_data["suite_name"],
            "description": bench_data["description"],
            "dataset_id": ds["id"],
            "dataset_version": 1,
            "rubric_json": bench_data["rubric"],
            "workspace_scope": "demo",
            "created_at": utcnow().isoformat(),
        }
        self.eval_cases[suite_id] = [
            {
                "id": str(uuid.uuid4()),
                "suite_id": suite_id,
                "prompt_text": c["prompt"],
                "reference_output": c["reference_output"],
                "expected_claims_json": c["expected_citations"],
            }
            for c in bench_data["cases"]
        ]

        baseline_agent_id = next(
            (a["id"] for a in self.agents.values() if a["slug"] == "universal-baseline"),
            None,
        )
        team_id = next(iter(self.agent_teams.keys()), None)

        # Execute Configuration A: Single-Agent Baseline
        eval_a = self.run_evaluation_benchmark(
            user_id=owner["id"],
            organization_id=org_id,
            suite_id=suite_id,
            label="Configuration A — Single-Agent Baseline",
            execution_mode="single_agent",
            agent_id=baseline_agent_id,
            workspace_scope="demo",
        )
        # Attach Configuration A & B runs to demo project as well
        self.launch_agent_run(
            user_id=owner["id"],
            organization_id=org_id,
            objective="Comparative Evaluation — Configuration B (8-Agent Specialized Team)",
            execution_mode="multi_agent_team",
            research_project_id=pid,
            team_id=team_id,
            workspace_scope="demo",
        )
        # Execute Configuration B: Multi-Agent Research Team against identical suite
        self.run_evaluation_benchmark(
            user_id=owner["id"],
            organization_id=org_id,
            suite_id=suite_id,
            label="Configuration B — Multi-Agent Research Team (8 Specialized Agents)",
            execution_mode="multi_agent_team",
            team_id=team_id,
            baseline_eval_run_id=eval_a["id"],
            workspace_scope="demo",
        )

        self.transition_research_project_state(
            user_id=owner["id"],
            organization_id=org_id,
            project_id=pid,
            target_state="COMPLETED",
        )

        # Create one pending R2 Governance Approval in demo scope so Approval Center is actionable immediately
        self.create_and_trigger_workflow(
            user_id=owner["id"],
            organization_id=org_id,
            name="Publikacja Zewnętrznego Pakietu Replikacyjnego (Demo R2 Gate)",
            description="Przepływ oczekujący na zatwierdzenie R2 w Centrum Zatwierdzeń.",
            risk_level="R2",
            require_approval=True,
        )
