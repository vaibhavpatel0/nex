export type Decision = "ALLOW" | "BLOCK" | "REQUIRE_APPROVAL";
export type Outcome = "PASS" | "FAIL" | "ERROR" | "INCONCLUSIVE" | "SKIPPED";

export interface RuntimeEvent {
  seq: number;
  event_id: string;
  correlation_id: string | null;
  run_id: string | null;
  agent_id: string | null;
  event_type: string;
  severity: "info" | "notice" | "warning" | "critical" | "error";
  summary: string;
  payload: Record<string, any>;
  created_at: string;
}

export interface Me {
  id: string;
  username: string;
  role: "admin" | "operator" | "viewer";
  permissions: string[];
}

export interface Agent {
  id: string;
  key: string;
  name: string;
  description: string;
  agent_type: "simulated" | "local_model" | "hosted_model";
  behavior: string | null;
  provider: string;
  model_id: string | null;
  system_prompt: string | null;
  enabled_tools: string[];
  revision: number;
  enabled: boolean;
  is_builtin: boolean;
  label: string;
  permission_profile: { id: string; key: string; name: string };
  connection: { state: string; detail: string; checked_at?: string };
  last_run: { id: string; status: string; created_at: string; counts?: Record<string, number> } | null;
  last_event: { seq: number; event_type: string; summary: string; created_at: string } | null;
  versions?: { revision: number; created_at: string; created_by: string; snapshot: any }[];
  updated_at: string;
}

export interface Metric {
  metric: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  sample_size: number;
  note: string;
  formula?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
