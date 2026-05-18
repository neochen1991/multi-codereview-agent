import axios from "axios";

// 所有前端页面共用同一个 axios 实例，统一 API 前缀和超时时间。
const api = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

export interface ReviewSummary {
  review_id: string;
  status: string;
  issue_count?: number;
  phase?: string;
  analysis_mode?: "standard" | "light";
  queue_position?: number;
  is_next_candidate?: boolean;
  queue_blocker_code?: string;
  queue_blocker_message?: string;
  blocking_review_id?: string;
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
  quality_summary?: {
    evidence_chain_issue_count?: number;
    quality_filtered_issue_count?: number;
    policy_comment_budget_filtered_count?: number;
  };
  impact_summary?: {
    graph_status?: string;
    risk_level?: string;
    impacted_file_count?: number;
    recommended_test_scope_count?: number;
    successful_context_target_count?: number;
    successful_impact_target_count?: number;
  };
  selected_experts?: string[];
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  updated_at?: string;
}

// 前端 API 类型集中定义在这里，保证页面和接口契约保持一致。

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
  finding_type: string;
  normalized_issue_type?: string;
  category_label?: string;
  severity: string;
  confidence: number;
  confidence_rationale?: string;
  file_path: string;
  line_start: number;
  evidence: string[];
  cross_file_evidence?: string[];
  assumptions?: string[];
  context_files?: string[];
  matched_rules?: string[];
  violated_guidelines?: string[];
  rule_based_reasoning?: string;
  verification_needed?: boolean;
  verification_plan?: string;
  design_alignment_status?: string;
  design_doc_titles?: string[];
  matched_design_points?: string[];
  missing_design_points?: string[];
  extra_implementation_points?: string[];
  design_conflicts?: string[];
  remediation_strategy?: string;
  remediation_suggestion: string;
  remediation_steps?: string[];
  code_excerpt: string;
  code_context?: FindingCodeContext;
  context_source?: string;
  evidence_chain?: EvidenceChainStep[];
  suggested_code?: string;
  suggested_code_language?: string;
  created_at: string;
}

export interface FindingCodeContextSnippet {
  path?: string;
  kind?: string;
  symbol?: string;
  line_start?: number;
  line_end?: number;
  snippet?: string;
}

export interface FindingCodeContextTransaction {
  kind?: string;
  transactional_method?: string;
  transactional_path?: string;
  transaction_boundary_snippet?: string;
  call_chain?: string[];
  contains_remote_call?: boolean;
  contains_message_publish?: boolean;
  contains_multi_repository_write?: boolean;
}

export interface FindingCodeContextHunk {
  file_path?: string;
  hunk_header?: string;
  start_line?: number;
  end_line?: number;
  changed_lines?: number[];
  excerpt?: string;
}

export interface FindingCodeContextSymbol {
  symbol?: string;
  definitions?: FindingCodeContextSnippet[];
  references?: FindingCodeContextSnippet[];
}

export interface FindingCodeContextInputCompleteness {
  review_spec_present?: boolean;
  language_guidance_present?: boolean;
  enabled_rule_count?: number;
  matched_rule_count?: number;
  bound_document_count?: number;
  target_file_diff_present?: boolean;
  source_context_present?: boolean;
  related_context_count?: number;
  missing_sections?: string[];
}

export interface FindingCodeContextMatchedRule {
  rule_id?: string;
  title?: string;
  priority?: string;
}

export interface FindingCodeContextReviewInputs {
  expert_id?: string;
  review_spec_present?: boolean;
  language_guidance_language?: string;
  language_guidance_present?: boolean;
  language_guidance_topics?: string[];
  bound_document_titles?: string[];
  matched_rules?: FindingCodeContextMatchedRule[];
  context_files?: string[];
}

export interface FindingRuleAttributionDetail {
  rule_id?: string;
  title?: string;
  priority?: string;
  scene_path?: string;
  reason?: string;
}

export interface FindingRuleAttribution {
  expert_id?: string;
  sources?: string[];
  general_rules?: string[];
  valid_custom_rule_ids?: string[];
  invalid_custom_rule_ids?: string[];
  custom_rule_details?: FindingRuleAttributionDetail[];
  available_custom_rule_ids?: string[];
  normalized_matched_rules?: string[];
  custom_rules_are_additive?: boolean;
}

export interface FindingCodeContext {
  target_file_full_diff?: string;
  related_diff_summary?: string;
  source_file_context?: string;
  problem_source_context?: FindingCodeContextSnippet;
  target_hunk?: FindingCodeContextHunk;
  primary_context?: FindingCodeContextSnippet;
  related_contexts?: FindingCodeContextSnippet[];
  related_source_snippets?: FindingCodeContextSnippet[];
  java_review_mode?: "general" | "ddd_enhanced" | string;
  java_context_signals?: string[];
  current_class_context?: FindingCodeContextSnippet & { class_name?: string; method_name?: string; changed_methods?: string[]; changed_fields?: string[] };
  parent_contract_contexts?: FindingCodeContextSnippet[];
  caller_contexts?: FindingCodeContextSnippet[];
  callee_contexts?: FindingCodeContextSnippet[];
  domain_model_contexts?: FindingCodeContextSnippet[];
  transaction_context?: FindingCodeContextTransaction;
  persistence_contexts?: FindingCodeContextSnippet[];
  symbol_contexts?: FindingCodeContextSymbol[];
  context_files?: string[];
  routing_reason?: string;
  code_graph_source_summary?: Record<string, unknown>;
  code_graph_minimal_context?: Record<string, unknown>;
  code_graph_impact_analysis?: Record<string, unknown>;
  code_graph_related_contexts?: FindingCodeContextSnippet[];
  code_graph_evidence_chain?: EvidenceChainStep[];
  input_completeness?: FindingCodeContextInputCompleteness;
  review_inputs?: FindingCodeContextReviewInputs;
  rule_attribution?: FindingRuleAttribution;
  candidate_verification?: Record<string, unknown>;
}

export interface DebateIssue {
  issue_id: string;
  canonical_issue_id?: string;
  review_id: string;
  title: string;
  summary: string;
  finding_type?: string;
  normalized_issue_type?: string;
  category_label?: string;
  aggregated_finding_types?: string[];
  file_path?: string;
  line_start?: number;
  status: string;
  severity: string;
  confidence: number;
  confidence_rationale?: string;
  confidence_breakdown?: Record<string, number | string | boolean>;
  finding_ids: string[];
  primary_expert_id?: string;
  participant_expert_ids: string[];
  supporting_expert_ids?: string[];
  expert_views?: Array<Record<string, unknown>>;
  aggregated_titles?: string[];
  aggregated_summaries?: string[];
  aggregated_remediation_strategies?: string[];
  aggregated_remediation_suggestions?: string[];
  aggregated_remediation_steps?: string[];
  remediation_strategy?: string;
  remediation_suggestion?: string;
  remediation_steps?: string[];
  current_code?: string;
  suggested_code?: string;
  evidence: string[];
  evidence_chain?: EvidenceChainStep[];
  needs_human: boolean;
  verified: boolean;
  needs_debate: boolean;
  verifier_name?: string;
  tool_name?: string;
  tool_verified?: boolean;
  sast_cross_validated?: boolean;
  sast_prescan_matches?: Record<string, unknown>[];
  human_decision: string;
  resolution?: string;
  consistency_check_status?: string;
  consistency_check_summary?: string;
  consistency_conflicts?: string[];
  remediation_alignment_status?: string;
  remediation_alignment_conflicts?: string[];
  remediation_filtered?: boolean;
  created_at: string;
  updated_at: string;
}

export interface EvidenceChainStep {
  step: string;
  status?: string;
  claim?: string;
  issue_id?: string;
  file_path?: string;
  line_start?: number;
  evidence?: string[];
  cross_file_evidence?: string[];
  reasons?: string[];
  tool_name?: string;
  tool_verified?: boolean;
  score?: number;
  summary?: string;
  signals?: string[];
  verdict?: string;
  confidence_adjustment?: number;
  reason?: string;
  original_confidence?: number;
  final_confidence?: number;
  confidence_delta?: number;
  false_positive_risk?: string;
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

export interface RuleScreeningMatchedRule {
  rule_id: string;
  title: string;
  priority: string;
  decision: string;
  reason: string;
  matched_terms: string[];
}

export interface RuleScreeningBatchInputRule {
  rule_id: string;
  title: string;
  priority: string;
}

export interface RuleScreeningBatchDecision {
  rule_id: string;
  title: string;
  priority: string;
  decision: string;
  reason: string;
  matched_terms: string[];
  matched_signals?: string[];
}

export interface RuleScreeningBatchMetadata {
  batch_index: number;
  batch_count: number;
  screening_mode?: "heuristic" | "llm" | string;
  input_rule_count: number;
  must_review_count: number;
  possible_hit_count: number;
  no_hit_count: number;
  input_rules: RuleScreeningBatchInputRule[];
  decisions: RuleScreeningBatchDecision[];
}

export interface RuleScreeningMetadata {
  total_rules: number;
  enabled_rules: number;
  must_review_count: number;
  possible_hit_count: number;
  matched_rule_count: number;
  batch_count?: number;
  screening_mode?: "heuristic" | "llm" | string;
  screening_fallback_used?: boolean;
  matched_rules_for_llm: RuleScreeningMatchedRule[];
}

export interface IssueFilterDecision {
  topic: string;
  rule_code: string;
  rule_label: string;
  reason: string;
  severity: string;
  finding_ids?: string[];
  finding_titles?: string[];
  expert_ids?: string[];
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

export interface ImpactFeedbackPayload {
  target_type: "impact_path" | "impact_file" | "test_scope" | string;
  target_key: string;
  label: "confirmed" | "false_positive" | "impact_confirmed" | "impact_false_positive" | string;
  comment?: string;
}

export interface ConfidenceSummary {
  high_confidence_count: number;
  debated_issue_count: number;
  needs_human_count: number;
  verified_issue_count: number;
  direct_defect_count?: number;
  risk_hypothesis_count?: number;
  test_gap_count?: number;
  design_concern_count?: number;
  llm_judged_issue_count?: number;
  llm_judge_accepted_count?: number;
  llm_judge_needs_verification_count?: number;
  llm_judge_needs_human_count?: number;
  llm_judge_rejected_count?: number;
  quality_filtered_issue_count?: number;
  evidence_chain_issue_count?: number;
  evidence_chain_coverage?: number;
  policy_comment_budget_filtered_count?: number;
  review_policy_excluded_file_count?: number;
  review_policy_reviewable_file_count?: number;
  review_policy_path_rule_count?: number;
  review_policy_required_expert_count?: number;
}

export interface LlmUsageSummary {
  total_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ImpactSymbol {
  file_path: string;
  symbol: string;
  kind: string;
  container: string;
  line_start: number;
}

export interface ImpactFile {
  file_path: string;
  relationship: string;
  reason: string;
  risk_level: string;
}

export interface ImpactPath {
  source: string;
  target: string;
  path: string[];
  depth: number;
  risk: string;
  confidence_label?: string;
  confirmation_reason?: string;
}

export interface ImpactGraphNode {
  node_id: string;
  label: string;
  kind: string;
  file_path: string;
  role: string;
  risk: string;
}

export interface ImpactGraphEdge {
  source: string;
  target: string;
  relationship: string;
  confidence: number;
}

export interface ImpactGraph {
  nodes: ImpactGraphNode[];
  edges: ImpactGraphEdge[];
}

export interface TestScopeRecommendation {
  scope: string;
  reason: string;
  paths: string[];
  priority: string;
}

export interface ImpactIssueLink {
  issue_id: string;
  issue_title: string;
  issue_file_path: string;
  impact_target: string;
  relationship: string;
  reason: string;
}

export interface ImpactReport {
  graph_status: string;
  graph_indexed_at?: string;
  graph_commit?: string;
  fact_source?: string;
  analysis_workflow?: string[];
  changed_files: string[];
  changed_symbols: ImpactSymbol[];
  impacted_files: ImpactFile[];
  impacted_modules: string[];
  impact_paths: ImpactPath[];
  impact_graph: ImpactGraph;
  external_entrypoints: string[];
  risk_level: string;
  recommended_test_scope: TestScopeRecommendation[];
  must_run_tests: string[];
  queried_targets?: string[];
  successful_context_targets?: string[];
  successful_impact_targets?: string[];
  dynamic_targets?: string[];
  skipped_invalid_targets?: string[];
  skipped_missing_context_targets?: string[];
  skipped_missing_impact_targets?: string[];
  manual_verification: string[];
  limitations: string[];
  report_summary?: string;
  key_impact_points?: string[];
  test_focus?: string[];
  related_issue_ids?: string[];
  impact_issue_links?: ImpactIssueLink[];
  llm_markdown?: string;
  llm_generated?: boolean;
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
  llm_usage_summary: LlmUsageSummary;
  human_review_status: string;
  issue_filter_decisions?: IssueFilterDecision[];
  impact_report?: ImpactReport | null;
}

export interface GovernanceMetrics {
  review_count: number;
  issue_count: number;
  tool_confirmation_rate: number;
  debate_survival_rate: number;
  needs_human_count: number;
  false_positive_count: number;
}

export interface RuntimeThresholdRecommendations {
  applied: boolean;
  should_tighten: boolean;
  recommended_thresholds: {
    issue_confidence_threshold_p1: number;
    issue_confidence_threshold_p2: number;
    issue_confidence_threshold_p3: number;
    hint_issue_confidence_threshold: number;
  };
  basis: Array<{
    group: string;
    key: string;
    sample_count: number;
    false_positive_rate: number;
  }>;
  reason: string;
}

export interface ReviewLearningCase {
  case_id: string;
  review_id: string;
  issue_id: string;
  repo_id: string;
  project_id: string;
  issue_type: string;
  decision: string;
  positive_count?: number;
  negative_count?: number;
  reason_category: string;
  file_path: string;
  line_start: number;
  learning_summary: string;
  counter_evidence: string;
  support_count: number;
  effect_level: "weak_hint" | "strong_hint" | "enforced" | string;
  match_count: number;
  last_matched_at: string;
  last_match_action: string;
  status: string;
  created_at: string;
}

export interface LlmTimeoutSample {
  timestamp: string;
  timeout_kind: string;
  provider: string;
  model: string;
  phase: string;
  review_id: string;
  expert_id: string;
  attempt_elapsed_ms: number;
  total_elapsed_ms: number;
}

export interface LlmTimeoutMetrics {
  timeout_count: number;
  connect_timeout_count: number;
  read_timeout_count: number;
  write_timeout_count: number;
  pool_timeout_count: number;
  other_timeout_count: number;
  success_count: number;
  avg_success_elapsed_ms: number;
  max_success_elapsed_ms: number;
  recent_timeouts: LlmTimeoutSample[];
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
  runtime_tool_bindings: string[];
  skill_bindings?: string[];
  skill_bindings_manual?: string[];
  skill_bindings_extension?: string[];
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
  review_spec: string;
}

export interface RuntimeSettings {
  config_path?: string;
  default_target_branch: string;
  default_analysis_mode: "standard" | "light";
  storage_backend: "sqlite" | "postgres";
  storage_pg_url: string;
  storage_pg_schema: string;
  storage_pg_user: string;
  storage_pg_password?: string;
  storage_pg_password_configured?: boolean;
  code_repo_clone_url: string;
  code_repo_local_path: string;
  code_repo_default_branch: string;
  code_repo_access_token?: string;
  code_repo_access_token_configured?: boolean;
  github_access_token?: string;
  github_access_token_configured?: boolean;
  gitlab_access_token?: string;
  gitlab_access_token_configured?: boolean;
  codehub_access_token?: string;
  codehub_access_token_configured?: boolean;
  code_repo_auto_sync: boolean;
  auto_review_enabled: boolean;
  auto_review_repo_url: string;
  auto_review_poll_interval_seconds: number;
  default_repository_id: string;
  code_repositories: CodeRepositorySettings[];
  database_sources: PostgresDataSourceSettings[];
  tool_allowlist: string[];
  mcp_allowlist: string[];
  runtime_tool_allowlist: string[];
  agent_allowlist: string[];
  allow_human_gate: boolean;
  issue_filter_enabled: boolean;
  issue_min_priority_level: "P0" | "P1" | "P2" | "P3";
  issue_confidence_threshold_p0: number;
  issue_confidence_threshold_p1: number;
  issue_confidence_threshold_p2: number;
  issue_confidence_threshold_p3: number;
  suppress_low_risk_hint_issues: boolean;
  hint_issue_confidence_threshold: number;
  hint_issue_evidence_cap: number;
  enable_llm_evidence_filter: boolean;
  llm_evidence_filter_confidence_threshold: number;
  llm_evidence_filter_timeout_seconds: number;
  enable_llm_issue_judge: boolean;
  llm_issue_judge_confidence_threshold: number;
  llm_issue_judge_timeout_seconds: number;
  rule_screening_mode: "heuristic" | "llm";
  rule_screening_batch_size: number;
  rule_screening_llm_timeout_seconds: number;
  enable_llm_targeted_debate: boolean;
  llm_targeted_debate_timeout_seconds: number;
  enable_sast_prescan: boolean;
  enable_review_workspace_realtime_graph: boolean;
  gitnexus_max_targets: number;
  gitnexus_max_context_queries: number;
  gitnexus_max_impact_queries: number;
  gitnexus_max_dynamic_targets: number;
  default_max_debate_rounds: number;
  standard_llm_timeout_seconds: number;
  standard_llm_retry_count: number;
  standard_max_parallel_experts: number;
  light_llm_timeout_seconds: number;
  light_llm_retry_count: number;
  light_max_parallel_experts: number;
  light_max_debate_rounds: number;
  light_llm_max_prompt_chars: number;
  light_llm_max_input_tokens: number;
  llm_log_truncate_enabled: boolean;
  llm_log_preview_limit: number;
  review_prompt_profile:
    | "auto"
    | "strict-json-small-context"
    | "rule-guided-standard"
    | "rule-guided-compact"
    | "long-context-capable"
    | "legacy";
  default_llm_provider: string;
  default_llm_base_url: string;
  default_llm_model: string;
  default_llm_api_key_env?: string | null;
  default_llm_api_key?: string;
  default_llm_api_key_configured?: boolean;
  allow_llm_fallback: boolean;
  verify_ssl: boolean;
  use_system_trust_store: boolean;
  ca_bundle_path: string;
}

export interface CodeRepositorySettings {
  repository_id: string;
  name?: string;
  provider?: "codehub" | "github" | "gitlab" | "generic";
  clone_url: string;
  web_url_prefixes?: string[];
  local_path: string;
  default_branch?: string;
  enabled?: boolean;
  auto_review_enabled?: boolean;
  auto_review_poll_interval_seconds?: number;
  auto_sync?: boolean;
  gitnexus_enabled?: boolean;
  database_source_ids?: string[];
}

export interface GitNexusIndexStatus {
  repository_id?: string;
  state: "idle" | "running" | "ready" | "failed" | "skipped" | "unknown" | string;
  message: string;
  gitnexus_installed?: boolean;
  gitnexus_command?: string;
  gitnexus_path?: string;
  updated_at?: string;
  indexed_at?: string;
  repo_path?: string;
  repo_name?: string;
  commit?: string;
  graph_dir?: string;
  graph_dir_exists?: boolean;
  registry_path?: string;
  registry_registered?: boolean;
  return_code?: number;
  stdout?: string;
  stderr?: string;
  trigger?: string;
  blocked_by_repository_id?: string;
}

export interface CodeGraphDependencyCheck {
  name: string;
  status: "passed" | "warning" | "failed" | string;
  message: string;
}

export interface CodeGraphIndexStatus {
  repository_id?: string;
  state: "idle" | "running" | "ready" | "failed" | "skipped" | "blocked" | "unknown" | string;
  message: string;
  updated_at?: string;
  indexed_at?: string;
  repo_path?: string;
  repo_name?: string;
  graph_dir?: string;
  graph_dir_exists?: boolean;
  graph_db_path?: string;
  graph_db_exists?: boolean;
  graph_db_updated_at?: string;
  graph_file_count?: number;
  graph_node_count?: number;
  graph_edge_count?: number;
  graph_db_readable?: boolean;
  selected_file_count?: number;
  indexed_file_count?: number;
  skipped_unchanged_file_count?: number;
  failed_file_count?: number;
  tree_sitter_installed?: boolean;
  tree_sitter_parser_available?: boolean;
  dependency_checks?: CodeGraphDependencyCheck[];
  languages?: string[];
  trigger?: string;
  blocked_by_repository_id?: string;
}

export interface GitNexusPreflightCheck {
  name: string;
  status: "passed" | "warning" | "failed" | string;
  message: string;
}

export interface GitNexusPreflightStatus {
  repository_id?: string;
  status: "ready" | "warning" | "failed" | string;
  checks: GitNexusPreflightCheck[];
  recommended_actions: string[];
}

export interface ReviewWorkspaceCleanupResult {
  root: string;
  older_than_days: number;
  removed_count: number;
  removed_paths: string[];
  failed: Array<{
    path: string;
    error: string;
  }>;
}

export interface ImpactFeedbackTargetProfile {
  target_id: string;
  target_type: string;
  target_key: string;
  sample_count: number;
  confirmed_count: number;
  false_positive_count: number;
  false_positive_rate: number;
  accept_rate: number;
  recommended_action: "require_manual_verification" | "boost_confidence" | "keep_observing" | string;
}

export interface ImpactFeedbackProfiles {
  summary: {
    sample_count: number;
    confirmed_count: number;
    false_positive_count: number;
    false_positive_rate: number;
    accept_rate: number;
  };
  targets: Record<string, ImpactFeedbackTargetProfile>;
}

export interface ImpactReportTemplate {
  template_path: string;
  default_template_path?: string;
  schema_path?: string;
  default_schema_path?: string;
  content: string;
  schema_content?: string;
  updated_at?: string;
  schema_updated_at?: string;
  placeholders?: string[];
  schema_variables?: Array<Record<string, any>>;
  undefined_placeholders?: string[];
  unused_variables?: string[];
}

export interface ImpactReportTemplatePreview {
  markdown: string;
  placeholders?: string[];
  schema_variables?: Array<Record<string, any>>;
  undefined_placeholders?: string[];
}

export interface ImpactReportTemplateAnalysis {
  schema_content: string;
  schema_variables?: Array<Record<string, any>>;
  placeholders?: string[];
  undefined_placeholders?: string[];
}

export interface PostgresDataSourceSettings {
  repo_url: string;
  provider: "postgres" | string;
  enabled: boolean;
  host: string;
  port: number;
  database: string;
  user: string;
  password_env: string;
  schema_allowlist: string[];
  ssl_mode: string;
  connect_timeout_seconds: number;
  statement_timeout_ms: number;
}

export interface ExtensionSkill {
  skill_id: string;
  name: string;
  description: string;
  bound_experts: string[];
  applicable_experts: string[];
  required_tools: string[];
  required_doc_types: string[];
  activation_hints: string[];
  required_context: string[];
  allowed_modes: string[];
  output_contract: Record<string, any>;
  prompt_body: string;
  skill_path?: string;
}

export interface ExtensionTool {
  tool_id: string;
  name: string;
  description: string;
  runtime: string;
  entry: string;
  timeout_seconds: number;
  allowed_experts: string[];
  bound_skills: string[];
  input_schema: Record<string, any>;
  output_schema: Record<string, any>;
  run_script: string;
  tool_path?: string;
}

export interface KnowledgeDocumentSection {
  node_id: string;
  doc_id: string;
  title: string;
  path: string;
  level: number;
  line_start: number;
  line_end: number;
  summary: string;
  content: string;
  score?: number;
  matched_terms?: string[];
  matched_signals?: string[];
}

export interface KnowledgeDocument {
  doc_id: string;
  title: string;
  expert_id: string;
  doc_type?: string;
  content: string;
  tags: string[];
  source_filename: string;
  storage_path: string;
  indexed_outline: string[];
  matched_sections: KnowledgeDocumentSection[];
  created_at: string;
}

export interface ReviewDesignDocumentInput {
  doc_id?: string;
  title: string;
  filename: string;
  content: string;
  doc_type?: "design_spec";
}

export interface ReviewReplayBundle {
  review: ReviewSummary;
  events: ReviewEvent[];
  messages: ConversationMessage[];
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

export interface CodehubExportItem {
  issue_id: string;
  title: string;
  severity: string;
  problem_description: string;
  remediation_suggestion: string;
  patched_code: string;
  mock_ticket_url: string;
  finding_ids: string[];
}

export interface CodehubExportResponse {
  review_id: string;
  status: string;
  submitted_count: number;
  items: CodehubExportItem[];
}

export interface BenchmarkExpectedResult {
  text: string;
  verdict: "pending" | "hit" | "missed" | "not_applicable";
  matched_issue_ids: string[];
  comment?: string;
}

export interface BenchmarkEvaluationPayload {
  expected_results: BenchmarkExpectedResult[];
  false_positive_issue_ids: string[];
  review_quality_score: number;
  impact_quality_score: number;
  notes: string;
}

export const reviewApi = {
  async create(payload: {
    subject_type: "mr" | "branch";
    analysis_mode?: "standard" | "light";
    source_ref?: string;
    target_ref?: string;
    title?: string;
    mr_url?: string;
    repo_url?: string;
    selected_experts?: string[];
    metadata?: Record<string, unknown>;
    design_docs?: ReviewDesignDocumentInput[];
  }): Promise<{ review_id: string; status: string }> {
    const { data } = await api.post("/reviews", payload);
    return data;
  },
  async list(): Promise<ReviewSummary[]> {
    const { data } = await api.get("/reviews");
    return data;
  },
  async listQueue(): Promise<ReviewSummary[]> {
    const { data } = await api.get("/reviews/queue");
    return data;
  },
  async syncQueue(): Promise<{
    enabled: boolean;
    repo_url: string;
    created_count: number;
    created_review_ids: string[];
    started_review_id: string;
    message?: string;
  }> {
    const { data } = await api.post("/reviews/queue/sync");
    return data;
  },
  async get(reviewId: string): Promise<ReviewSummary> {
    const { data } = await api.get(`/reviews/${reviewId}`);
    return data;
  },
  async getSnapshot(reviewId: string): Promise<ReviewSummary> {
    const { data } = await api.get(`/reviews/${reviewId}/snapshot`);
    return data;
  },
  async start(reviewId: string): Promise<{ review_id: string; status: string; phase: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/start`);
    return data;
  },
  async queueStart(reviewId: string): Promise<{ review_id: string; status: string; phase: string; message: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/queue-start`);
    return data;
  },
  async close(reviewId: string): Promise<{ review_id: string; status: string; phase: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/close`);
    return data;
  },
  async rerun(reviewId: string): Promise<{ review_id: string; status: string; phase: string; message: string }> {
    const { data } = await api.post(`/reviews/${reviewId}/rerun`);
    return data;
  },
  async delete(reviewId: string): Promise<{ review_id: string; status: string }> {
    const { data } = await api.delete(`/reviews/${reviewId}`);
    return data;
  },
  async batchDelete(reviewIds: string[]): Promise<{
    deleted_review_ids: string[];
    deleted_count: number;
    compaction_scheduled: boolean;
  }> {
    const { data } = await api.post("/reviews/batch-delete", { review_ids: reviewIds });
    return data;
  },
  async saveBenchmarkEvaluation(reviewId: string, payload: BenchmarkEvaluationPayload): Promise<ReviewSummary> {
    const { data } = await api.post(`/reviews/${reviewId}/benchmark-evaluation`, payload);
    return data;
  },
  async listEvents(
    reviewId: string,
    options?: { since?: string; limit?: number },
  ): Promise<ReviewEvent[]> {
    const { data } = await api.get(`/reviews/${reviewId}/events`, {
      params: options || {},
    });
    return data;
  },
  async listFindings(
    reviewId: string,
    options?: { since?: string; limit?: number },
  ): Promise<ReviewFinding[]> {
    const { data } = await api.get(`/reviews/${reviewId}/findings`, {
      params: options || {},
    });
    return data;
  },
  async getFinding(reviewId: string, findingId: string): Promise<ReviewFinding> {
    const { data } = await api.get(`/reviews/${reviewId}/findings/${findingId}`);
    return data;
  },
  async listMessages(
    reviewId: string,
    options?: { since?: string; limit?: number },
  ): Promise<ConversationMessage[]> {
    const { data } = await api.get(`/reviews/${reviewId}/messages`, {
      params: options || {},
    });
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
  async getReport(
    reviewId: string,
    options?: {
      findings_limit?: number;
      findings_offset?: number;
      issues_limit?: number;
      issues_offset?: number;
    },
  ): Promise<ReviewReport> {
    const { data } = await api.get(`/reviews/${reviewId}/report`, {
      params: options || {},
    });
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
  ): Promise<{
    review_id: string;
    status: string;
    phase: string;
    human_review_status: string;
    learning_recorded?: boolean;
    learning_effect?: "confirmed_sample" | "false_positive_sample" | string;
  }> {
    const { data } = await api.post(`/reviews/${reviewId}/human-decisions`, payload);
    return data;
  },
  async submitImpactFeedback(reviewId: string, payload: ImpactFeedbackPayload): Promise<FeedbackLabel> {
    const { data } = await api.post(`/reviews/${reviewId}/impact-feedback`, payload);
    return data;
  },
  async exportIssuesToCodehub(
    reviewId: string,
    payload: { issue_ids: string[] },
  ): Promise<CodehubExportResponse> {
    const { data } = await api.post(`/reviews/${reviewId}/issues/export/codehub`, payload);
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
    runtime_tool_bindings: string[];
    agent_bindings: string[];
    max_tool_calls?: number;
    max_debate_rounds?: number;
    provider?: string | null;
    api_base_url?: string | null;
    api_key?: string | null;
    api_key_env?: string | null;
    model?: string | null;
    system_prompt: string;
    review_spec?: string;
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
    runtime_tool_bindings: string[];
    agent_bindings: string[];
    max_tool_calls?: number;
    max_debate_rounds?: number;
    provider?: string | null;
    api_base_url?: string | null;
    api_key?: string | null;
    api_key_env?: string | null;
    model?: string | null;
    system_prompt: string;
    review_spec?: string;
  }): Promise<ExpertProfile> {
    const { data } = await api.put(`/experts/${expertId}`, payload);
    return data;
  },
  async remove(expertId: string): Promise<void> {
    await api.delete(`/experts/${expertId}`);
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
    doc_type?: string;
    content: string;
    tags?: string[];
    source_filename?: string;
  }): Promise<KnowledgeDocument> {
    const { data } = await api.post("/knowledge/upload", payload);
    return data;
  },
  async remove(docId: string): Promise<void> {
    await api.delete(`/knowledge/${docId}`);
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
  async getLlmTimeoutMetrics(): Promise<LlmTimeoutMetrics> {
    const { data } = await api.get("/governance/llm-timeout-metrics");
    return data;
  },
  async getRuntimeThresholdRecommendations(): Promise<RuntimeThresholdRecommendations> {
    const { data } = await api.get("/governance/runtime-threshold-recommendations");
    return data;
  },
  async getImpactFeedbackProfiles(): Promise<ImpactFeedbackProfiles> {
    const { data } = await api.get("/governance/impact-feedback-profiles");
    return data;
  },
  async getReviewLearningCases(): Promise<ReviewLearningCase[]> {
    const { data } = await api.get("/governance/review-learning-cases");
    return data;
  },
  async updateReviewLearningCaseStatus(caseId: string, status: "active" | "disabled"): Promise<ReviewLearningCase> {
    const { data } = await api.patch(`/governance/review-learning-cases/${caseId}`, { status });
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
  async getGitNexusIndexStatus(): Promise<GitNexusIndexStatus> {
    const { data } = await api.get("/settings/gitnexus/index/status");
    return data;
  },
  async runGitNexusIndex(): Promise<GitNexusIndexStatus> {
    const { data } = await api.post("/settings/gitnexus/index/run");
    return data;
  },
  async getGitNexusPreflight(): Promise<GitNexusPreflightStatus> {
    const { data } = await api.get("/settings/gitnexus/preflight");
    return data;
  },
  async getRepositoryGitNexusIndexStatus(repositoryId: string): Promise<GitNexusIndexStatus> {
    const { data } = await api.get(`/settings/repositories/${encodeURIComponent(repositoryId)}/gitnexus/status`);
    return data;
  },
  async runRepositoryGitNexusIndex(repositoryId: string): Promise<GitNexusIndexStatus> {
    const { data } = await api.post(`/settings/repositories/${encodeURIComponent(repositoryId)}/gitnexus/index/run`);
    return data;
  },
  async getRepositoryGitNexusPreflight(repositoryId: string): Promise<GitNexusPreflightStatus> {
    const { data } = await api.get(`/settings/repositories/${encodeURIComponent(repositoryId)}/gitnexus/preflight`);
    return data;
  },
  async getCodeGraphIndexStatus(): Promise<CodeGraphIndexStatus> {
    const { data } = await api.get("/settings/code-graph/index/status");
    return data;
  },
  async runCodeGraphIndex(): Promise<CodeGraphIndexStatus> {
    const { data } = await api.post("/settings/code-graph/index/run");
    return data;
  },
  async getRepositoryCodeGraphIndexStatus(repositoryId: string): Promise<CodeGraphIndexStatus> {
    const { data } = await api.get(`/settings/repositories/${encodeURIComponent(repositoryId)}/code-graph/status`);
    return data;
  },
  async runRepositoryCodeGraphIndex(repositoryId: string): Promise<CodeGraphIndexStatus> {
    const { data } = await api.post(`/settings/repositories/${encodeURIComponent(repositoryId)}/code-graph/index/run`);
    return data;
  },
  async cleanupReviewWorkspaces(olderThanDays = 7): Promise<ReviewWorkspaceCleanupResult> {
    const { data } = await api.post("/settings/review-workspaces/cleanup", null, { params: { older_than_days: olderThanDays } });
    return data;
  },
  async getImpactReportTemplate(): Promise<ImpactReportTemplate> {
    const { data } = await api.get("/settings/impact-report-template");
    return data;
  },
  async updateImpactReportTemplate(content: string, schemaContent: string): Promise<ImpactReportTemplate> {
    const { data } = await api.put("/settings/impact-report-template", { content, schema_content: schemaContent });
    return data;
  },
  async resetImpactReportTemplate(): Promise<ImpactReportTemplate> {
    const { data } = await api.post("/settings/impact-report-template/reset");
    return data;
  },
  async previewImpactReportTemplate(content: string, schemaContent: string): Promise<ImpactReportTemplatePreview> {
    const { data } = await api.post("/settings/impact-report-template/preview", { content, schema_content: schemaContent });
    return data;
  },
  async analyzeImpactReportTemplate(content: string): Promise<ImpactReportTemplateAnalysis> {
    const { data } = await api.post("/settings/impact-report-template/analyze", { content });
    return data;
  },
  async listExtensionSkills(): Promise<ExtensionSkill[]> {
    const { data } = await api.get("/settings/extensions/skills");
    return data;
  },
  async upsertExtensionSkill(skillId: string, payload: ExtensionSkill): Promise<ExtensionSkill> {
    const { data } = await api.put(`/settings/extensions/skills/${skillId}`, payload);
    return data;
  },
  async listExtensionTools(): Promise<ExtensionTool[]> {
    const { data } = await api.get("/settings/extensions/tools");
    return data;
  },
  async upsertExtensionTool(toolId: string, payload: ExtensionTool): Promise<ExtensionTool> {
    const { data } = await api.put(`/settings/extensions/tools/${toolId}`, payload);
    return data;
  },
};

// 过程页通过这个方法统一构造 SSE 地址，避免各页面手写路径。
export const buildReviewEventStreamUrl = (reviewId: string): string =>
  `${window.location.origin}/api/reviews/${reviewId}/events/stream`;

export default api;
