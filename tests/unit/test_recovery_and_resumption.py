"""Recovery & durable checkpoint resumption tests (Part VIII.4)."""

from __future__ import annotations

from apex_one.platform.workspace_service import ApexPlatformService


def test_interrupted_multi_agent_run_resumes_without_repeating_completed_tools() -> None:
    svc = ApexPlatformService(seed_demo=True)
    owner_id = svc.users_by_email["admin@apex-one.local"]
    org_id = svc.default_org_id

    proj = svc.create_research_project(
        user_id=owner_id,
        organization_id=org_id,
        title="Test Wznawiania po Awarii",
    )
    svc.register_source(
        user_id=owner_id,
        organization_id=org_id,
        source_type="MD",
        title="Dokument Testowy Odporności",
        uri_or_filename="resilience.md",
        raw_text="Trwały zapis punktów kontrolnych eliminuje duplikację wywołań narzędzi.",
        research_project_id=proj["id"],
    )

    # Interrupt deliberately after step 3 of the 8-agent pipeline
    interrupted = svc.launch_agent_run(
        user_id=owner_id,
        organization_id=org_id,
        objective="Zbadaj odporność punktów kontrolnych",
        execution_mode="multi_agent_team",
        research_project_id=proj["id"],
        interrupt_after_step=3,
    )
    assert interrupted["status"] == "INTERRUPTED"
    assert interrupted["checkpoint_json"]["last_completed_step"] == 3
    tools_before = list(interrupted["tool_invocations"])
    assert len(tools_before) == 3

    # Resume run from durable checkpoint — steps 1..3 MUST NOT re-invoke tools
    resumed = svc.resume_interrupted_agent_run(
        user_id=owner_id,
        organization_id=org_id,
        run_id=interrupted["id"],
    )
    assert resumed["status"] == "COMPLETED"
    assert resumed["progress_pct"] == 100
    all_idem_keys = [t["idempotency_key"] for t in resumed["tool_invocations"]]
    # Exactly 8 steps => 8 unique tool invocations (zero duplicates!)
    assert len(all_idem_keys) == 8
    assert len(set(all_idem_keys)) == 8


def test_workflow_approval_wait_state_survives_and_resumes_cleanly() -> None:
    svc = ApexPlatformService(seed_demo=False)
    user = svc.register_user(
        email="lead@lab.org",
        password="SafePassword!123",
        display_name="Lead Researcher",
        organization_name="Resilience Lab",
    )
    org_id = svc.list_user_organizations(user["id"])[0]["organization_id"]

    bundle = svc.create_and_trigger_workflow(
        user_id=user["id"],
        organization_id=org_id,
        name="R2 Controlled Export",
        description="Waits for explicit approval",
        risk_level="R2",
        require_approval=True,
    )
    wf_run_id = bundle["run"]["id"]
    approval_id = bundle["run"]["approval_id"]
    assert bundle["run"]["status"] == "AWAITING_APPROVAL"

    svc.decide_approval(
        user_id=user["id"],
        organization_id=org_id,
        approval_id=approval_id,
        decision="APPROVED",
        decision_note="Verified parameters",
    )
    completed = svc.resume_approved_workflow_run(
        user_id=user["id"],
        organization_id=org_id,
        workflow_run_id=wf_run_id,
    )
    assert completed["status"] == "COMPLETED"
