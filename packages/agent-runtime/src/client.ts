import type {
  EnvironmentStatusResponse,
  SandboxJobResponse,
  SandboxJobSubmission,
} from "./contracts.js";

/**
 * Browser-safe and Node-compatible API client for APEX ONE Research Edition.
 * Defaults to relative URLs (`""`) so Arena Live Preview never calls localhost/127.0.0.1
 * from the user's browser; Vite dev server proxies `/api` to FastAPI inside the sandbox.
 */
export class ApexOneClient {
  private readonly baseUrl: string;

  constructor(baseUrl = "") {
    if (
      typeof window !== "undefined" &&
      (baseUrl.includes("localhost") || baseUrl.includes("127.0.0.1"))
    ) {
      throw new Error(
        "Browser-facing code must never call localhost/127.0.0.1 directly; use relative '/api' URLs."
      );
    }
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async getEnvironmentStatus(): Promise<EnvironmentStatusResponse> {
    const resp = await fetch(`${this.baseUrl}/api/v1/environment/status`);
    if (!resp.ok) {
      throw new Error(`Environment status failed with HTTP ${resp.status}`);
    }
    return (await resp.json()) as EnvironmentStatusResponse;
  }

  async validateSandboxJob(
    tenantId: string,
    job: SandboxJobSubmission
  ): Promise<SandboxJobResponse> {
    const resp = await fetch(`${this.baseUrl}/api/v1/sandbox/jobs/validate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Tenant-Id": tenantId,
      },
      body: JSON.stringify(job),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Sandbox validation failed (${resp.status}): ${text}`);
    }
    return (await resp.json()) as SandboxJobResponse;
  }

  async executeSandboxJob(
    tenantId: string,
    job: SandboxJobSubmission
  ): Promise<{ httpStatus: number; body: unknown }> {
    const resp = await fetch(`${this.baseUrl}/api/v1/sandbox/jobs/execute`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Tenant-Id": tenantId,
      },
      body: JSON.stringify(job),
    });
    const body: unknown = await resp.json();
    return { httpStatus: resp.status, body };
  }
}
