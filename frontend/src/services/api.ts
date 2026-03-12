import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

export interface ReviewSummary {
  review_id: string;
  status: string;
  phase?: string;
  failure_reason?: string;
  human_review_status?: string;
  pending_human_issue_ids?: string[];
  report_summary?: string;
  subject: {
    subject_type: string;
    repo_id: string;
    project_id: string;
    source_ref: string;
    target_ref: string;
    title?: string;
    mr_url?: string;
    unified_diff?: string;
    changed_files?: string[];
    metadata?: Record<string, unknown>;
  };
  selected_experts?: string[];
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  updated_at?: string;
}

export interface ReviewEvent {
  event_id: string;
  review_id: string;
  event_type: string;
  phase: string;
  message: string;
  created_at: string;
  payload?: Record<string, unknown>;
}

export interface ReviewFinding {
  finding_id: string;
  review_id: string;
  expert_id: string;
  title: string;
  summary: string;
  severity: string;
  confidence: number;
  file_path: string;
  line_start: number;
  evidence: string[];
  remediation_suggestion: string;
  code_excerpt: string;
  created_at: string;
}

export interface DebateIssue {
  issue_id: string;
  review_id: string;
  title: string;
  summary: string;
  file_path?: string;
  line_start?: number;
  status: string;
  severity: string;
  confidence: number;
  finding_ids: string[];
  participant_expert_ids: string[];
  evidence: string[];
  needs_human: boolean;
  verified: boolean;
  needs_debate: boolean;
  verifier_name?: string;
  tool_name?: string;
  tool_verified?: boolean;
  human_decision: string;
  resolution?: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  message_id: string;
  review_id: string;
  issue_id: string;
  expert_id: string;
  message_type: string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface FeedbackLabel {
  label_id: string;
  review_id: string;
  issue_id: string;
  label: string;
  source: string;
  comment: string;
  created_at: string;
}

export interface ConfidenceSummary {
  high_confidence_count: number;
  debated_issue_count: number;
  needs_human_count: number;
  verified_issue_count: number;
}

export interface ReviewReport {
  review_id: string;
  status: string;
  phase: string;
  summary: string;
  findings: ReviewFinding[];
  issues: DebateIssue[];
  issue_count: number;
  confidence_summary: ConfidenceSummary;
  human_review_status: string;
}

export interface GovernanceMetrics {
  review_count: number;
  issue_count: number;
  tool_confirmation_rate: number;
  debate_survival_rate: number;
  needs_human_count: number;
  false_positive_count: number;
}

export interface ExpertMetricRow {
  expert_id: string;
  issue_count: number;
  tool_verified_count: number;
  debated_issue_count: number;
  accepted_risk_count: number;
  false_positive_count: number;
  human_approved_count: number;
}

export interface ExpertProfile {
  expert_id: string;
  name: string;
  name_zh: string;
  role: string;
  enabled: boolean;
  focus_areas: string[];
  activation_hints: string[];
  required_checks: string[];
  out_of_scope: string[];
  preferred_artifacts: string[];
  knowledge_sources: string[];
  tool_bindings: string[];
  mcp_tools: string[];
  skill_bindings: string[];
  agent_bindings: string[];
  max_tool_calls: number;
  max_debate_rounds: number;
  custom: boolean;
  provider?: string | null;
  api_base_url?: string | null;
  api_key?: string | null;
  api_key_env?: string | null;
  model?: string | null;
  system_prompt: string;
}

export interface RuntimeSettings {
  default_target_branch: string;
  tool_allowlist: string[];
  mcp_allowlist: string[];
  skill_allowlist: string[];
  agent_allowlist: string[];
  allow_human_gate: boolean;
  default_max_debate_rounds: number;
  default_llm_provider: string;
  default_llm_base_url: string;
  default_llm_model: string;
  default_llm_api_key_env?: string | null;
  default_llm_api_key?: string;
  default_llm_api_key_configured?: boolean;
  allow_llm_fallback: boolean;
}

export interface KnowledgeDocument {
  doc_id: string;
  title: string;
  expert_id: string;
  content: string;
  tags: string[];
  source_filename: string;
  storage_path: string;
  created_at: string;
}

export interface ReviewReplayBundle {
  review: ReviewSummary;
  events: ReviewEvent[];
  issues: DebateIssue[];
  messages: ConversationMessage[];
  report: ReviewReport;
  feedback_labels: FeedbackLabel[];
}

export interface ReviewArtifacts {
  summary_comment?: {
    review_id: string;
    title: string;
    summary: string;
    issue_count: number;
    human_review_status: string;
  };
  check_run?: {
    name: string;
    status: string;
    conclusion: string;
    details_url: string;
    issues: string[];
  };
  report_snapshot?: {
    review_id: string;
    status: string;
    phase: string;
    pending_human_issue_ids: string[];
    updated_at: string;
  };
}

export const reviewApi = {
  async create(payload: {
    subject_type: "mr" | "branch";
    repo_id?: string;
    project_id?: string;
    source_ref?: string;
    target_ref?: string;
    title?: string;
    mr_url?: string;
    repo_url?: string;
    access_token?: string;
    selected_experts?: string[];
  }): Promise<{ review_id: string; status: string }> {
    const { data } = await api.post("/reviews", payload);
    return data;
  },
  async list(): Promise<ReviewSummary[]> {
    const { data } = await api.get("/reviews");
    return data;
  },
  async get(reviewId: string): Promise<ReviewSummary> {
    const { data } = await api.get(`/reviews/${reviewId}`);
    return data;
  },
  async start(reviewId: string): Promise<{ review_id: string; status: string; phase: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/start`);
    return data;
  },
  async listEvents(reviewId: string): Promise<ReviewEvent[]> {
    const { data } = await api.get(`/reviews/${reviewId}/events`);
    return data;
  },
  async listFindings(reviewId: string): Promise<ReviewFinding[]> {
    const { data } = await api.get(`/reviews/${reviewId}/findings`);
    return data;
  },
  async listIssues(reviewId: string): Promise<DebateIssue[]> {
    const { data } = await api.get(`/reviews/${reviewId}/issues`);
    return data;
  },
  async listIssueMessages(reviewId: string, issueId: string): Promise<ConversationMessage[]> {
    const { data } = await api.get(`/reviews/${reviewId}/issues/${issueId}/messages`);
    return data;
  },
  async getReport(reviewId: string): Promise<ReviewReport> {
    const { data } = await api.get(`/reviews/${reviewId}/report`);
    return data;
  },
  async getReplay(reviewId: string): Promise<ReviewReplayBundle> {
    const { data } = await api.get(`/reviews/${reviewId}/replay`);
    return data;
  },
  async getArtifacts(reviewId: string): Promise<ReviewArtifacts> {
    const { data } = await api.get(`/reviews/${reviewId}/artifacts`);
    return data;
  },
  async submitHumanDecision(
    reviewId: string,
    payload: { issue_id: string; decision: "approved" | "rejected"; comment: string },
  ): Promise<{ review_id: string; status: string; phase: string; human_review_status: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/human-decisions`, payload);
    return data;
  },
};

export const expertApi = {
  async list(): Promise<ExpertProfile[]> {
    const { data } = await api.get("/experts");
    return data;
  },
  async create(payload: {
    expert_id: string;
    name: string;
    name_zh: string;
    role: string;
    enabled?: boolean;
    focus_areas: string[];
    activation_hints?: string[];
    required_checks?: string[];
    out_of_scope?: string[];
    preferred_artifacts?: string[];
    knowledge_sources: string[];
    tool_bindings: string[];
    mcp_tools: string[];
    skill_bindings: string[];
    agent_bindings: string[];
    max_tool_calls?: number;
    max_debate_rounds?: number;
    provider?: string | null;
    api_base_url?: string | null;
    api_key?: string | null;
    api_key_env?: string | null;
    model?: string | null;
    system_prompt: string;
  }): Promise<ExpertProfile> {
    const { data } = await api.post("/experts", payload);
    return data;
  },
  async update(expertId: string, payload: {
    expert_id: string;
    name: string;
    name_zh: string;
    role: string;
    enabled?: boolean;
    focus_areas: string[];
    activation_hints?: string[];
    required_checks?: string[];
    out_of_scope?: string[];
    preferred_artifacts?: string[];
    knowledge_sources: string[];
    tool_bindings: string[];
    mcp_tools: string[];
    skill_bindings: string[];
    agent_bindings: string[];
    max_tool_calls?: number;
    max_debate_rounds?: number;
    provider?: string | null;
    api_base_url?: string | null;
    api_key?: string | null;
    api_key_env?: string | null;
    model?: string | null;
    system_prompt: string;
  }): Promise<ExpertProfile> {
    const { data } = await api.put(`/experts/${expertId}`, payload);
    return data;
  },
};

export const knowledgeApi = {
  async list(): Promise<KnowledgeDocument[]> {
    const { data } = await api.get("/knowledge");
    return data;
  },
  async grouped(): Promise<Record<string, KnowledgeDocument[]>> {
    const { data } = await api.get("/knowledge/grouped");
    return data;
  },
  async retrieve(expertId: string, changedFiles: string[]): Promise<KnowledgeDocument[]> {
    const { data } = await api.get("/knowledge/retrieve", {
      params: { expert_id: expertId, changed_files: changedFiles },
      paramsSerializer: {
        serialize: (params) => {
          const query = new URLSearchParams();
          query.set("expert_id", String(params.expert_id));
          for (const file of params.changed_files || []) {
            query.append("changed_files", String(file));
          }
          return query.toString();
        },
      },
    });
    return data;
  },
  async uploadMarkdown(payload: {
    title: string;
    expert_id: string;
    content: string;
    tags?: string[];
    source_filename?: string;
  }): Promise<KnowledgeDocument> {
    const { data } = await api.post("/knowledge/upload", payload);
    return data;
  },
};

export const governanceApi = {
  async getQualityMetrics(): Promise<GovernanceMetrics> {
    const { data } = await api.get("/governance/quality-metrics");
    return data;
  },
  async getExpertMetrics(): Promise<ExpertMetricRow[]> {
    const { data } = await api.get("/governance/expert-metrics");
    return data;
  },
};

export const settingsApi = {
  async getRuntime(): Promise<RuntimeSettings> {
    const { data } = await api.get("/settings/runtime");
    return data;
  },
  async updateRuntime(payload: RuntimeSettings): Promise<RuntimeSettings> {
    const { data } = await api.put("/settings/runtime", payload);
    return data;
  },
};

export const buildReviewEventStreamUrl = (reviewId: string): string =>
  `${window.location.origin}/api/reviews/${reviewId}/events/stream`;

export default api;
