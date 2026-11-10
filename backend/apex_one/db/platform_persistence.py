"""Production PostgreSQL + pgvector + RLS persistence coordinator for ApexPlatformService.

When `APEX_DATABASE_URL` / `DATABASE_URL` is configured:
1. Hydrates `ApexPlatformService` on startup from PostgreSQL (`users`, `organizations`,
   RLS-isolated `organization_workspace_states`, `memories`, `memory_chunks` with
   `VECTOR(1536)`, and append-only `audit_events`) — or persists the initial workspace
   on an empty database without duplicating data across restarts.
2. Synchronizes mutating API operations transactionally into PostgreSQL under strict
   per-organization `SET LOCAL app.current_organization_id` / `app.current_tenant_id` RLS.
3. Executes semantic + lexical hybrid memory search directly against PostgreSQL
   `pgvector` (`memory_chunks.embedding <=> :query_vec`) inside an RLS-scoped session.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.base import utcnow
from apex_one.db.models import DEFAULT_EMBEDDING_DIM, ResearchProject, Tenant
from apex_one.db.platform_models import (
    AuditEventRecord,
    MemoryChunkRecord,
    MemoryObjectRecord,
    Organization,
    OrganizationWorkspaceStateRecord,
    PlatformIdentityDirectoryRecord,
    User,
)

if TYPE_CHECKING:
    from apex_one.platform.workspace_service import ApexPlatformService

IDENTITY_SINGLETON_KEY = "primary"
_TOKEN_RE = re.compile(r"[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}")


def _as_uuid(raw: str | uuid.UUID) -> uuid.UUID:
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, str(raw))


def _parse_dt(raw: str | None) -> datetime:
    if not raw:
        return utcnow()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()


def export_identity_bundle(svc: ApexPlatformService) -> dict[str, Any]:
    return {
        "default_org_id": svc.default_org_id,
        "users": svc.users,
        "users_by_email": svc.users_by_email,
        "organizations": svc.organizations,
        "sessions_by_hash": svc.sessions_by_hash,
    }


def export_org_bundle(svc: ApexPlatformService, org_id: str) -> dict[str, Any]:
    proj_ids = {
        pid
        for pid, p in svc.projects.items()
        if p.get("organization_id") == org_id
    }
    agent_ids = {
        aid
        for aid, a in svc.agents.items()
        if a.get("organization_id") == org_id
    }
    source_ids = {
        sid
        for sid, s in svc.sources.items()
        if s.get("organization_id") == org_id
    }
    dataset_ids = {
        did
        for did, d in svc.datasets.items()
        if d.get("organization_id") == org_id
    }
    suite_ids = {
        esid
        for esid, es in svc.eval_suites.items()
        if es.get("organization_id") == org_id
    }
    eval_run_ids = {
        erid
        for erid, er in svc.eval_runs.items()
        if er.get("organization_id") == org_id
    }
    mem_ids = {
        mid
        for mid, m in svc.memories.items()
        if m.get("organization_id") == org_id
    }

    # Strip heavy 1536-float arrays from JSONB snapshot because chunks live natively
    # in PostgreSQL `memory_chunks` (`VECTOR(1536)`); keep lightweight chunk metadata
    # and reconstruct from `memory_chunks` on hydrate.
    compact_chunks: dict[str, list[dict[str, Any]]] = {}
    for mid in mem_ids:
        compact_chunks[mid] = [
            {k: v for k, v in ch.items() if k != "embedding"}
            for ch in svc.memory_chunks.get(mid, [])
        ]

    return {
        "memberships": [
            m for m in svc.memberships if m.get("organization_id") == org_id
        ],
        "invitations": {
            k: v
            for k, v in svc.invitations.items()
            if v.get("organization_id") == org_id
        },
        "api_credentials": {
            k: v
            for k, v in svc.api_credentials.items()
            if v.get("organization_id") == org_id
        },
        "devices": {
            k: v
            for k, v in svc.devices.items()
            if v.get("organization_id") == org_id
        },
        "agents": {aid: svc.agents[aid] for aid in agent_ids},
        "agent_versions": {
            aid: svc.agent_versions.get(aid, []) for aid in agent_ids
        },
        "agent_teams": {
            k: v
            for k, v in svc.agent_teams.items()
            if v.get("organization_id") == org_id
        },
        "agent_runs": {
            k: v
            for k, v in svc.agent_runs.items()
            if v.get("organization_id") == org_id
        },
        "tools_catalog": svc.tools_catalog.get(org_id, []),
        "projects": {pid: svc.projects[pid] for pid in proj_ids},
        "questions": {pid: svc.questions.get(pid, []) for pid in proj_ids},
        "briefs": {pid: svc.briefs.get(pid, []) for pid in proj_ids},
        "plans": {pid: svc.plans.get(pid, []) for pid in proj_ids},
        "hypotheses": {pid: svc.hypotheses.get(pid, []) for pid in proj_ids},
        "reports": {pid: svc.reports.get(pid, []) for pid in proj_ids},
        "sources": {sid: svc.sources[sid] for sid in source_ids},
        "source_snapshots": {
            sid: svc.source_snapshots.get(sid, []) for sid in source_ids
        },
        "claims": {
            k: v
            for k, v in svc.claims.items()
            if v.get("organization_id") == org_id
        },
        "evidence": {
            k: v
            for k, v in svc.evidence.items()
            if v.get("organization_id") == org_id
        },
        "memories": {mid: svc.memories[mid] for mid in mem_ids},
        "memory_chunks_meta": compact_chunks,
        "datasets": {did: svc.datasets[did] for did in dataset_ids},
        "dataset_versions": {
            did: svc.dataset_versions.get(did, []) for did in dataset_ids
        },
        "experiments": {
            k: v
            for k, v in svc.experiments.items()
            if v.get("organization_id") == org_id
        },
        "experiment_runs": {
            k: v
            for k, v in svc.experiment_runs.items()
            if v.get("organization_id") == org_id
        },
        "eval_suites": {esid: svc.eval_suites[esid] for esid in suite_ids},
        "eval_cases": {
            esid: svc.eval_cases.get(esid, []) for esid in suite_ids
        },
        "eval_runs": {erid: svc.eval_runs[erid] for erid in eval_run_ids},
        "eval_scores": {
            erid: svc.eval_scores.get(erid, []) for erid in eval_run_ids
        },
        "regressions": [
            r for r in svc.regressions if r.get("organization_id") == org_id
        ],
        "workflow_defs": {
            k: v
            for k, v in svc.workflow_defs.items()
            if v.get("organization_id") == org_id
        },
        "workflow_runs_store": {
            k: v
            for k, v in svc.workflow_runs_store.items()
            if v.get("organization_id") == org_id
        },
        "approvals": {
            k: v
            for k, v in svc.approvals.items()
            if v.get("organization_id") == org_id
        },
        "budgets": svc.budgets.get(org_id, []),
        "usage_records": [
            u for u in svc.usage_records if u.get("organization_id") == org_id
        ],
        "model_providers": svc.model_providers.get(org_id, []),
        "extensions": svc.extensions.get(org_id, []),
    }


def _reset_service_collections(svc: ApexPlatformService) -> None:
    svc.users.clear()
    svc.users_by_email.clear()
    svc.organizations.clear()
    svc.memberships.clear()
    svc.invitations.clear()
    svc.sessions_by_hash.clear()
    svc.api_credentials.clear()
    svc.devices.clear()
    svc.agents.clear()
    svc.agent_versions.clear()
    svc.agent_teams.clear()
    svc.agent_runs.clear()
    svc.tools_catalog.clear()
    svc.projects.clear()
    svc.questions.clear()
    svc.briefs.clear()
    svc.plans.clear()
    svc.hypotheses.clear()
    svc.reports.clear()
    svc.sources.clear()
    svc.source_snapshots.clear()
    svc.claims.clear()
    svc.evidence.clear()
    svc.memories.clear()
    svc.memory_chunks.clear()
    svc.datasets.clear()
    svc.dataset_versions.clear()
    svc.experiments.clear()
    svc.experiment_runs.clear()
    svc.eval_suites.clear()
    svc.eval_cases.clear()
    svc.eval_runs.clear()
    svc.eval_scores.clear()
    svc.regressions.clear()
    svc.workflow_defs.clear()
    svc.workflow_runs_store.clear()
    svc.approvals.clear()
    svc.budgets.clear()
    svc.usage_records.clear()
    svc.model_providers.clear()
    svc.extensions.clear()


class PostgresPlatformPersistence:
    """Authoritative PostgreSQL + pgvector + RLS persistence bridge for ApexPlatformService."""

    def __init__(self, adapter: PostgresConnectionAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self.active: bool = False

    async def hydrate_or_seed(self, svc: ApexPlatformService) -> bool:
        """Load persisted platform state from PostgreSQL or persist initial seed."""
        if not self._adapter.is_configured:
            self.active = False
            return False

        health = await self._adapter.health_check()
        if not health.connected:
            self.active = False
            return False

        async with self._lock:
            try:
                async with self._adapter.session() as sys_session:
                    ident_row = await sys_session.get(
                        PlatformIdentityDirectoryRecord, IDENTITY_SINGLETON_KEY
                    )
                    org_rows = (
                        await sys_session.execute(select(Organization))
                    ).scalars().all()
            except Exception:
                # Schema may not have migration 0003 applied yet in partial test setups
                self.active = False
                return False

            if ident_row is None or not org_rows:
                # Empty database: persist initial workspace state into PostgreSQL
                self.active = True
                await self._write_all_unlocked(svc)
                return True

            # Existing database: hydrate in-memory coordinator from PostgreSQL
            _reset_service_collections(svc)
            ident = dict(ident_row.identity_json or {})
            svc.default_org_id = str(ident.get("default_org_id") or "")
            svc.users.update(ident.get("users") or {})
            svc.users_by_email.update(ident.get("users_by_email") or {})
            svc.organizations.update(ident.get("organizations") or {})
            svc.sessions_by_hash.update(ident.get("sessions_by_hash") or {})

            # Overlay authoritative relational Organization flags (e.g. emergency_stop)
            for org_row in org_rows:
                oid = str(org_row.id)
                if oid in svc.organizations:
                    svc.organizations[oid]["name"] = org_row.name
                    svc.organizations[oid]["slug"] = org_row.slug
                    svc.organizations[oid]["emergency_stop_active"] = bool(
                        org_row.emergency_stop_active
                    )

            for org_id in list(svc.organizations.keys()):
                org_uuid = _as_uuid(org_id)
                async with self._adapter.session_with_rls(org_uuid) as org_session:
                    ws_row = await org_session.get(
                        OrganizationWorkspaceStateRecord, org_uuid
                    )
                    if ws_row and ws_row.state_json:
                        b = ws_row.state_json
                        svc.memberships.extend(b.get("memberships") or [])
                        svc.invitations.update(b.get("invitations") or {})
                        svc.api_credentials.update(b.get("api_credentials") or {})
                        svc.devices.update(b.get("devices") or {})
                        svc.agents.update(b.get("agents") or {})
                        svc.agent_versions.update(b.get("agent_versions") or {})
                        svc.agent_teams.update(b.get("agent_teams") or {})
                        svc.agent_runs.update(b.get("agent_runs") or {})
                        svc.tools_catalog[org_id] = list(b.get("tools_catalog") or [])
                        svc.projects.update(b.get("projects") or {})
                        svc.questions.update(b.get("questions") or {})
                        svc.briefs.update(b.get("briefs") or {})
                        svc.plans.update(b.get("plans") or {})
                        svc.hypotheses.update(b.get("hypotheses") or {})
                        svc.reports.update(b.get("reports") or {})
                        svc.sources.update(b.get("sources") or {})
                        svc.source_snapshots.update(b.get("source_snapshots") or {})
                        svc.claims.update(b.get("claims") or {})
                        svc.evidence.update(b.get("evidence") or {})
                        svc.memories.update(b.get("memories") or {})
                        svc.datasets.update(b.get("datasets") or {})
                        svc.dataset_versions.update(b.get("dataset_versions") or {})
                        svc.experiments.update(b.get("experiments") or {})
                        svc.experiment_runs.update(b.get("experiment_runs") or {})
                        svc.eval_suites.update(b.get("eval_suites") or {})
                        svc.eval_cases.update(b.get("eval_cases") or {})
                        svc.eval_runs.update(b.get("eval_runs") or {})
                        svc.eval_scores.update(b.get("eval_scores") or {})
                        svc.regressions.extend(b.get("regressions") or [])
                        svc.workflow_defs.update(b.get("workflow_defs") or {})
                        svc.workflow_runs_store.update(
                            b.get("workflow_runs_store") or {}
                        )
                        svc.approvals.update(b.get("approvals") or {})
                        svc.budgets[org_id] = list(b.get("budgets") or [])
                        svc.usage_records.extend(b.get("usage_records") or [])
                        svc.model_providers[org_id] = list(
                            b.get("model_providers") or []
                        )
                        svc.extensions[org_id] = list(b.get("extensions") or [])

                    # Hydrate pgvector memory chunks from `memory_chunks`
                    db_chunks = (
                        await org_session.execute(
                            select(MemoryChunkRecord).order_by(
                                MemoryChunkRecord.chunk_index.asc()
                            )
                        )
                    ).scalars().all()
                    for ch_row in db_chunks:
                        mid_str = str(ch_row.memory_id)
                        svc.memory_chunks.setdefault(mid_str, []).append(
                            {
                                "id": str(ch_row.id),
                                "memory_id": mid_str,
                                "organization_id": org_id,
                                "chunk_index": ch_row.chunk_index,
                                "content": ch_row.content,
                                "embedding": list(ch_row.embedding),
                                "source_citation": ch_row.source_citation,
                                "metadata": dict(ch_row.metadata_json or {}),
                                "created_at": ch_row.created_at.isoformat(),
                            }
                        )

                    # Hydrate immutable SHA-256 audit chain from `audit_events`
                    audit_rows = (
                        await org_session.execute(
                            select(AuditEventRecord).order_by(
                                AuditEventRecord.sequence_no.asc()
                            )
                        )
                    ).scalars().all()
                    svc.audit.hydrate_org_entries(
                        org_id,
                        [
                            {
                                "id": str(ar.id),
                                "organization_id": org_id,
                                "sequence_no": ar.sequence_no,
                                "actor_id": ar.actor_id,
                                "actor_type": ar.actor_type,
                                "action": ar.action,
                                "resource_type": ar.resource_type,
                                "resource_id": ar.resource_id,
                                "risk_level": ar.risk_level,
                                "details": {
                                    k: v
                                    for k, v in dict(ar.details_json or {}).items()
                                    if k != "_canonical_created_at"
                                },
                                "prev_hash": ar.prev_hash,
                                "entry_hash": ar.entry_hash,
                                "created_at": (
                                    ar.details_json.get("_canonical_created_at")
                                    if isinstance(ar.details_json, dict)
                                    and "_canonical_created_at" in ar.details_json
                                    else ar.created_at.isoformat()
                                ),
                            }
                            for ar in audit_rows
                        ],
                    )

            self.active = True
            return True

    async def sync_to_postgres(self, svc: ApexPlatformService) -> None:
        """Atomically synchronize identity, RLS org workspaces, pgvector chunks & audit log."""
        if not self.active:
            return
        async with self._lock:
            await self._write_all_unlocked(svc)

    async def _write_all_unlocked(self, svc: ApexPlatformService) -> None:
        now = utcnow()
        # 1. System-level identity directory (`users`, `organizations`, `platform_identity_directory`)
        async with self._adapter.session() as sys_session:
            for uid, u in svc.users.items():
                u_uuid = _as_uuid(uid)
                existing_u = await sys_session.get(User, u_uuid)
                if existing_u is None:
                    sys_session.add(
                        User(
                            id=u_uuid,
                            email=u["email"],
                            password_hash=u["password_hash"],
                            display_name=u["display_name"],
                            locale=u.get("locale", "pl"),
                            preferences_json=dict(u.get("preferences") or {}),
                            is_active=bool(u.get("is_active", True)),
                        )
                    )
                else:
                    existing_u.display_name = u["display_name"]
                    existing_u.locale = u.get("locale", "pl")
                    existing_u.preferences_json = dict(u.get("preferences") or {})
                    existing_u.is_active = bool(u.get("is_active", True))
                    existing_u.updated_at = now

            for oid, org in svc.organizations.items():
                o_uuid = _as_uuid(oid)
                existing_o = await sys_session.get(Organization, o_uuid)
                if existing_o is None:
                    sys_session.add(
                        Organization(
                            id=o_uuid,
                            slug=org["slug"],
                            name=org["name"],
                            policies_json=dict(org.get("policies") or {}),
                            emergency_stop_active=bool(
                                org.get("emergency_stop_active", False)
                            ),
                        )
                    )
                else:
                    existing_o.name = org["name"]
                    existing_o.policies_json = dict(org.get("policies") or {})
                    existing_o.emergency_stop_active = bool(
                        org.get("emergency_stop_active", False)
                    )
                    existing_o.updated_at = now

                # Mirror organization into `tenants` table so `research_projects` FK holds
                existing_t = await sys_session.get(Tenant, o_uuid)
                if existing_t is None:
                    sys_session.add(
                        Tenant(
                            id=o_uuid,
                            slug=org["slug"],
                            name=org["name"],
                        )
                    )
                else:
                    existing_t.name = org["name"]
                    existing_t.updated_at = now

            ident_row = await sys_session.get(
                PlatformIdentityDirectoryRecord, IDENTITY_SINGLETON_KEY
            )
            ident_payload = export_identity_bundle(svc)
            if ident_row is None:
                sys_session.add(
                    PlatformIdentityDirectoryRecord(
                        singleton_key=IDENTITY_SINGLETON_KEY,
                        identity_json=ident_payload,
                    )
                )
            else:
                ident_row.identity_json = ident_payload
                ident_row.updated_at = now

        # 2. Per-organization RLS-enforced synchronization
        for oid in list(svc.organizations.keys()):
            org_uuid = _as_uuid(oid)
            org_bundle = export_org_bundle(svc, oid)

            async with self._adapter.session_with_rls(org_uuid) as org_session:
                # 2a. Workspace state record (RLS-protected)
                ws_row = await org_session.get(
                    OrganizationWorkspaceStateRecord, org_uuid
                )
                if ws_row is None:
                    org_session.add(
                        OrganizationWorkspaceStateRecord(
                            organization_id=org_uuid,
                            state_json=org_bundle,
                        )
                    )
                else:
                    ws_row.state_json = org_bundle
                    ws_row.updated_at = now

                # 2b. Relational `research_projects`
                for pid, proj in org_bundle["projects"].items():
                    p_uuid = _as_uuid(pid)
                    existing_p = await org_session.get(ResearchProject, p_uuid)
                    if existing_p is None:
                        org_session.add(
                            ResearchProject(
                                id=p_uuid,
                                tenant_id=org_uuid,
                                title=proj["title"],
                                hypothesis=proj.get("hypothesis", ""),
                                status=proj.get("state", "CREATED"),
                                metadata_json={
                                    "workspace_scope": proj.get(
                                        "workspace_scope", "research"
                                    ),
                                    "version": proj.get("version", 1),
                                },
                            )
                        )
                    else:
                        existing_p.title = proj["title"]
                        existing_p.hypothesis = proj.get("hypothesis", "")
                        existing_p.status = proj.get("state", "CREATED")
                        existing_p.updated_at = now

                # 2c. Relational `memories` + `memory_chunks` (`pgvector` VECTOR(1536))
                current_mem_uuids = {
                    _as_uuid(mid) for mid in org_bundle["memories"].keys()
                }
                existing_mem_ids = set(
                    (
                        await org_session.execute(select(MemoryObjectRecord.id))
                    ).scalars().all()
                )
                removed_mem_ids = existing_mem_ids - current_mem_uuids
                if removed_mem_ids:
                    await org_session.execute(
                        delete(MemoryObjectRecord).where(
                            MemoryObjectRecord.id.in_(removed_mem_ids)
                        )
                    )

                for mid, mem in org_bundle["memories"].items():
                    m_uuid = _as_uuid(mid)
                    owner_uuid = _as_uuid(
                        mem.get("owner_id") or str(uuid.UUID(int=1))
                    )
                    proj_uuid = (
                        _as_uuid(mem["project_id"])
                        if mem.get("project_id")
                        else None
                    )
                    existing_m = await org_session.get(MemoryObjectRecord, m_uuid)
                    needs_chunk_sync = existing_m is None
                    if existing_m is None:
                        org_session.add(
                            MemoryObjectRecord(
                                id=m_uuid,
                                organization_id=org_uuid,
                                owner_id=owner_uuid,
                                project_id=proj_uuid,
                                memory_type=mem["memory_type"],
                                title=mem["title"],
                                content=mem["content"],
                                source_ref=mem.get("source_ref", "local://note"),
                                access_policy_json=dict(
                                    mem.get("access_policy") or {}
                                ),
                                version=int(mem.get("version", 1)),
                                retention_state=mem.get("retention_state", "active"),
                                retention_days=mem.get("retention_days"),
                                metadata_json=dict(mem.get("metadata") or {}),
                                workspace_scope=mem.get(
                                    "workspace_scope", "research"
                                ),
                            )
                        )
                        await org_session.flush()
                    elif existing_m.version != int(mem.get("version", 1)):
                        existing_m.version = int(mem.get("version", 1))
                        existing_m.title = mem["title"]
                        existing_m.content = mem["content"]
                        existing_m.updated_at = now
                        await org_session.execute(
                            delete(MemoryChunkRecord).where(
                                MemoryChunkRecord.memory_id == m_uuid
                            )
                        )
                        needs_chunk_sync = True

                    if needs_chunk_sync:
                        for ch in svc.memory_chunks.get(mid, []):
                            emb = list(ch.get("embedding") or [])
                            if len(emb) != DEFAULT_EMBEDDING_DIM:
                                continue
                            org_session.add(
                                MemoryChunkRecord(
                                    id=_as_uuid(ch["id"]),
                                    organization_id=org_uuid,
                                    memory_id=m_uuid,
                                    chunk_index=int(ch["chunk_index"]),
                                    content=ch["content"],
                                    embedding=emb,
                                    source_citation=ch["source_citation"],
                                    metadata_json=dict(ch.get("metadata") or {}),
                                )
                            )

                # 2d. Append-only `audit_events` synchronization (honors trg_audit_events_immutable)
                max_seq = (
                    await org_session.execute(
                        select(func.coalesce(func.max(AuditEventRecord.sequence_no), 0))
                    )
                ).scalar_one()
                for entry in svc.audit.get_chronological_entries(oid):
                    if entry.sequence_no > int(max_seq):
                        details_with_ts = dict(entry.details or {})
                        details_with_ts["_canonical_created_at"] = entry.created_at
                        org_session.add(
                            AuditEventRecord(
                                id=_as_uuid(entry.id),
                                organization_id=org_uuid,
                                sequence_no=entry.sequence_no,
                                actor_id=entry.actor_id,
                                actor_type=entry.actor_type,
                                action=entry.action,
                                resource_type=entry.resource_type,
                                resource_id=entry.resource_id,
                                risk_level=entry.risk_level,
                                details_json=details_with_ts,
                                prev_hash=entry.prev_hash,
                                entry_hash=entry.entry_hash,
                                created_at=_parse_dt(entry.created_at),
                            )
                        )

    async def pgvector_hybrid_search(
        self,
        *,
        organization_id: str,
        query: str,
        query_vector: list[float] | None = None,
        query_embedding: list[float] | None = None,
        memory_type: str | None = None,
        project_id: str | None = None,
        scope: str | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        """Execute real PostgreSQL pgvector HNSW cosine distance + lexical hybrid search."""
        vec = query_embedding if query_embedding is not None else (query_vector or [])
        org_uuid = _as_uuid(organization_id)
        q_tokens = {t.lower() for t in _TOKEN_RE.findall(query)}
        distance_expr = MemoryChunkRecord.embedding.cosine_distance(vec).label(
            "cosine_distance"
        )
        stmt = (
            select(MemoryChunkRecord, MemoryObjectRecord, distance_expr)
            .join(
                MemoryObjectRecord,
                MemoryChunkRecord.memory_id == MemoryObjectRecord.id,
            )
            .where(MemoryObjectRecord.retention_state == "active")
        )
        if memory_type:
            stmt = stmt.where(MemoryObjectRecord.memory_type == memory_type)
        if project_id:
            stmt = stmt.where(MemoryObjectRecord.project_id == _as_uuid(project_id))
        if scope and scope != "all":
            stmt = stmt.where(MemoryObjectRecord.workspace_scope == scope)

        stmt = stmt.order_by(distance_expr.asc()).limit(max(top_k * 3, 20))

        async with self._adapter.session_with_rls(org_uuid) as session:
            rows = (await session.execute(stmt)).all()

        scored: list[dict[str, Any]] = []
        for chunk_row, mem_row, dist in rows:
            semantic_score = max(0.0, 1.0 - float(dist))
            c_tokens = {t.lower() for t in _TOKEN_RE.findall(chunk_row.content)}
            lexical = (
                len(q_tokens & c_tokens) / len(q_tokens)
                if q_tokens and c_tokens
                else 0.0
            )
            hybrid = round(0.65 * semantic_score + 0.35 * lexical, 4)
            scored.append(
                {
                    "id": str(chunk_row.id),
                    "chunk_id": str(chunk_row.id),
                    "memory_id": str(mem_row.id),
                    "memory_type": mem_row.memory_type,
                    "title": mem_row.title,
                    "memory_title": mem_row.title,
                    "chunk_index": chunk_row.chunk_index,
                    "content": chunk_row.content,
                    "excerpt": chunk_row.content[:420],
                    "source_citation": chunk_row.source_citation,
                    "source_ref": mem_row.source_ref,
                    "semantic_score": round(semantic_score, 4),
                    "lexical_score": round(lexical, 4),
                    "hybrid_score": hybrid,
                    "score": hybrid,
                    "search_backend": "postgresql-pgvector-hnsw",
                }
            )
        scored.sort(key=lambda r: r["hybrid_score"], reverse=True)
        return scored[:top_k]
