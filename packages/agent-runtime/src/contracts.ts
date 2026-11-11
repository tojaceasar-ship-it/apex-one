export type EnvironmentVerdict = "READY" | "CONDITIONAL" | "BLOCKED";

export interface DatabaseStatus {
  configured: boolean;
  connected: boolean;
  dsn_redacted: string;
  server_version: string | null;
  pgvector_available: boolean;
  pgvector_installed: boolean;
  pgvector_version: string | null;
  current_role: string | null;
  is_superuser_or_bypassrls: boolean | null;
  error: string | null;
}

export interface CacheStatus {
  enabled: boolean;
  configured: boolean;
  connected: boolean;
  dsn_redacted: string;
  mode: string;
  error: string | null;
}

export interface SandboxStatus {
  provider: string;
  isolation_verified: boolean;
  arbitrary_code_execution_ready: boolean;
  reason: string;
}

export interface HybridLocalNodeStatus {
  protocol_version: string;
  arena_isolated_mode: boolean;
  user_machine_connected: boolean;
  loopback_protocol_sessions: number;
  note: string;
}

export interface EnvironmentStatusResponse {
  verdict: EnvironmentVerdict;
  environment_profile: "development" | "staging" | "production";
  docker_required_for_dev: boolean;
  apt_required_for_dev: boolean;
  env_var_presence: Record<string, boolean>;
  postgresql: DatabaseStatus;
  redis: CacheStatus;
  worker_outbox: {
    mode: string;
    daemon_assumed: boolean;
    docker_required: boolean;
    features: string[];
  };
  experiment_sandbox: SandboxStatus;
  hybrid_local_node: HybridLocalNodeStatus;
  conditions: string[];
}

export interface SandboxJobSubmission {
  runtime: "python3.11" | "node22" | "r4";
  entrypoint: string[];
  inputs?: Array<{ relative_path: string; content_utf8: string }>;
  env_vars?: Record<string, string>;
}

export interface SandboxJobResponse {
  job_id: string;
  tenant_id: string;
  provider: string;
  isolation_verified: boolean;
  state: string;
  error_reason?: string | null;
}

export type RiskLevel = "R0" | "R1" | "R2" | "R3";

export type ResearchState =
  | "CREATED"
  | "PLANNING"
  | "RESEARCHING"
  | "ANALYZING"
  | "HYPOTHESIS_FORMATION"
  | "EXPERIMENT_DESIGN"
  | "AWAITING_APPROVAL"
  | "EXECUTING"
  | "VERIFYING"
  | "REPORTING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ClaimStatus =
  | "supported"
  | "partially_supported"
  | "contradicted"
  | "unresolved"
  | "rejected";

export interface AgentDNA {
  instructions: string;
  model_policy: Record<string, unknown>;
  capabilities: string[];
  allowed_tools: string[];
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  memory_policy: Record<string, unknown>;
  execution_policy: Record<string, unknown>;
  budget: { max_tokens: number; max_cost_usd: number };
  max_steps: number;
  timeout: number;
  evaluation_profile: string;
}

export interface AgentRuntimeAdapterDescriptor {
  adapter_name: string;
  supports_durable_checkpoints: boolean;
  supports_idempotent_tool_ledger: boolean;
  supports_sse_streaming: boolean;
}

