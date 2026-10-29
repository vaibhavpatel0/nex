import type { AgentEvent } from "../lib/live";

export interface RunSummary {
  correlation_id: string;
  kind: string;
  conversation_id: string | null;
  customer_id: string | null;
  started_by: string;
  message: string;
  status: string;
  fixture: string;
  aegis_run_id: string | null;
  scenario_key: string | null;
  enforcement: string;
  model_provider: string;
  model: string;
  final_text: string | null;
  error_kind: string | null;
  error: string | null;
  usage: { prompt_tokens: number; completion_tokens: number } | null;
  tool_calls: number;
  blocked_calls: number;
  started_at: string;
  ended_at: string | null;
}

export interface RunDetail {
  run: RunSummary;
  events: AgentEvent[];
  pending_actions: { approval_id: string; tool: string; status: string; fixture: string; executed_by: string | null; result: any }[];
}

export const STATES = ["RUNNING", "WAITING_FOR_MODEL", "TOOL_REQUESTED", "POLICY_CHECKING", "BLOCKED", "WAITING_FOR_APPROVAL", "TOOL_EXECUTING", "COMPLETED", "FAILED"];
export const AGENT_KEY = "shop-assistant";
