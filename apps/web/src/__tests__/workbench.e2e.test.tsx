// @vitest-environment jsdom
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { App } from "../App";

describe("APEX ONE Workbench End-to-End UI Flows", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.pushState({}, "", "/login");

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || "GET";

      const jsonRes = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: {
            "Content-Type": "application/json",
            "X-Request-ID": "req-e2e-ui",
          },
        });

      if (url.endsWith("/api/v1/auth/login") && method === "POST") {
        return jsonRes({
          access_token: "tok-e2e",
          csrf_token: "csrf-e2e",
          active_organization_id: "org-1",
          user: {
            id: "u-1",
            email: "admin@apex-one.local",
            display_name: "Dr Anna Kowalska (Owner)",
            locale: "pl",
          },
          organizations: [
            {
              organization_id: "org-1",
              slug: "apex-lab",
              name: "APEX ONE Research Laboratory",
              role: "Owner",
              emergency_stop_active: false,
            },
          ],
        });
      }

      if (url.endsWith("/api/v1/auth/me")) {
        return jsonRes({
          user: {
            id: "u-1",
            email: "admin@apex-one.local",
            display_name: "Dr Anna Kowalska (Owner)",
            locale: "pl",
          },
          active_organization_id: "org-1",
          role: "Owner",
          organizations: [
            {
              organization_id: "org-1",
              slug: "apex-lab",
              name: "APEX ONE Research Laboratory",
              role: "Owner",
              emergency_stop_active: false,
            },
          ],
        });
      }

      if (url.includes("/api/v1/analytics/summary")) {
        return jsonRes({
          agent_execution_count: 3,
          successful_executions: 3,
          failure_rate_pct: 0,
          avg_execution_time_ms: 410,
          total_model_cost_usd: 0.0234,
          total_tokens_consumed: 7760,
          tool_invocation_count: 17,
          workflow_total: 1,
          workflow_succeeded: 0,
          pending_approvals_count: 1,
          budget_limit_usd: 250,
          budget_utilization_pct: 0.01,
          benchmark_comparisons: [
            {
              id: "ev-a",
              label: "Configuration A — Single-Agent Baseline",
              tokens_used: 1840,
              scores: { research_accuracy: 0.41, evidence_quality: 0.6, cost_efficiency: 1.0 },
            },
            {
              id: "ev-b",
              label: "Configuration B — Multi-Agent Research Team",
              tokens_used: 6120,
              scores: { research_accuracy: 1.0, evidence_quality: 1.0, cost_efficiency: 0.33 },
            },
          ],
          usage_series: [],
        });
      }

      if (url.includes("/api/v1/research/projects") && method === "GET") {
        return jsonRes({
          total: 1,
          items: [
            {
              id: "proj-demo-1",
              title: "Comparative Evaluation of Single-Agent and Multi-Agent Research Systems",
              state: "COMPLETED",
              version: 9,
              workspace_scope: "demo",
              scope_summary: "Controlled benchmark study",
            },
          ],
        });
      }

      if (url.endsWith("/api/v1/environment/status")) {
        return jsonRes({
          verdict: "CONDITIONAL",
          postgresql: { connected: false },
          experiment_sandbox: { isolation_verified: false },
          hybrid_local_node: { user_machine_connected: false },
        });
      }

      if (url.endsWith("/api/v1/governance")) {
        return jsonRes({
          emergency_stop_active: false,
          approvals: [
            {
              id: "ap-1",
              operation_type: "workflow.execute:demo",
              risk_level: "R2",
              status: "PENDING",
              parameters_sha256: "abcdef1234567890abcdef1234567890",
              parameters_json: { workflow: "demo" },
              expires_at: "2026-11-11T12:00:00Z",
            },
          ],
          audit_events: [
            {
              id: "au-1",
              sequence_no: 1,
              action: "auth.login",
              resource_type: "session",
              resource_id: "sess-12345678",
              risk_level: "R0",
              entry_hash: "99887766554433221100998877665544",
            },
          ],
          audit_chain_integrity: { valid: true, entries_checked: 1 },
        });
      }

      if (url.includes("/api/v1/governance/approvals/ap-1/decide")) {
        return jsonRes({ id: "ap-1", status: "APPROVED" });
      }

      return jsonRes({ items: [], total: 0 });
    }) as any;
  });

  it("renders Polish login by default, signs in, displays Command Center KPIs, and navigates to Approval Center", async () => {
    render(<App />);

    // Default Polish UI on /login
    expect(
      await screen.findByText("Zaloguj się do APEX ONE Workbench")
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Zaloguj się/i }));

    // Redirects to Command Center and renders seeded Comparative Evaluation project & KPIs
    expect(
      await screen.findByText(
        "Comparative Evaluation of Single-Agent and Multi-Agent Research Systems"
      )
    ).toBeInTheDocument();
    expect(screen.getByText(/7,760|7\s*760|7760/)).toBeInTheDocument();

    // Navigate to Approval Center via sidebar and approve pending R2 request
    const approvalsLinks = screen.getAllByText("Centrum Zatwierdzeń");
    fireEvent.click(approvalsLinks[0]);

    expect(await screen.findByText("workflow.execute:demo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Zatwierdź/i }));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/v1/governance/approvals/ap-1/decide",
        expect.objectContaining({ method: "POST" })
      );
    });
  });
});
