import type { DebateIssue, EvidenceChainStep, ReviewFinding } from "@/services/api";

export type IssueCallChainGraph = {
  chart: string;
  sourceLabel: string;
  summary: string;
  nodes: string[];
};

type UnknownRecord = Record<string, unknown>;

const compact = (value: unknown): string => String(value || "").trim();

const isRecord = (value: unknown): value is UnknownRecord =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

const dedupe = (values: string[]): string[] => {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values.map((item) => item.trim()).filter(Boolean)) {
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(value);
  }
  return result;
};

const labelFromSnippet = (value: unknown): string => {
  if (!isRecord(value)) return compact(value);
  return compact(
    value.symbol ||
      value.qualified_name ||
      value.name ||
      value.entrypoint ||
      value.changed_node ||
      value.target ||
      value.source ||
      value.path ||
      value.file_path,
  );
};

const pathFromUnknown = (value: unknown): string[] => {
  if (typeof value === "string") {
    return value
      .split(/\s*(?:->|=>|→|,|，)\s*/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (!isRecord(value)) return [];
  for (const key of ["path", "call_chain", "nodes", "flow"]) {
    const raw = value[key];
    if (Array.isArray(raw)) {
      const labels = raw.map(labelFromSnippet).filter(Boolean);
      if (labels.length >= 2) return labels;
    }
    if (typeof raw === "string") {
      const labels = pathFromUnknown(raw);
      if (labels.length >= 2) return labels;
    }
  }
  const source = labelFromSnippet(value.source);
  const changed = labelFromSnippet(value.changed_node);
  const target = labelFromSnippet(value.target);
  const entrypoint = labelFromSnippet(value.entrypoint);
  return dedupe([entrypoint || source, changed, target].filter(Boolean));
};

const evidencePaths = (steps: EvidenceChainStep[]): string[][] => {
  const paths: string[][] = [];
  for (const step of steps) {
    const payload = step as EvidenceChainStep & UnknownRecord;
    const stepName = compact(payload.step);
    if (!["graph_relationship", "affected_flows", "related_contexts"].includes(stepName)) continue;
    for (const key of ["flows", "affected_flows", "contexts", "related_contexts", "relationships"]) {
      const raw = payload[key];
      if (Array.isArray(raw)) {
        for (const item of raw) {
          const path = pathFromUnknown(item);
          if (path.length >= 2) paths.push(path);
        }
      }
    }
    const directPath = pathFromUnknown(payload);
    if (directPath.length >= 2) paths.push(directPath);
  }
  return paths;
};

const contextPaths = (finding: ReviewFinding, issue: DebateIssue | null): string[][] => {
  const context = finding.code_context || {};
  const paths: string[][] = [];
  const transactionPath = context.transaction_context?.call_chain || [];
  if (transactionPath.length >= 2) paths.push(transactionPath.map(compact).filter(Boolean));

  const current =
    compact(context.current_class_context?.method_name) ||
    compact(context.current_class_context?.class_name) ||
    compact(issue?.title) ||
    compact(finding.title);
  const callers = dedupe((context.caller_contexts || []).map(labelFromSnippet).filter(Boolean));
  const callees = dedupe((context.callee_contexts || []).map(labelFromSnippet).filter(Boolean));
  if (current && (callers.length || callees.length)) {
    const left = callers.slice(0, 3);
    const right = callees.slice(0, 4);
    paths.push([...left, current, ...right].filter(Boolean));
  }

  const impact = context.code_graph_impact_analysis as UnknownRecord | undefined;
  const affectedFlows = Array.isArray(impact?.affected_flows) ? impact?.affected_flows || [] : [];
  for (const flow of affectedFlows) {
    const path = pathFromUnknown(flow);
    if (path.length >= 2) paths.push(path);
  }
  return paths;
};

const sanitizeMermaidText = (value: string): string =>
  value.replace(/[<>{}[\]|"`]/g, " ").replace(/\s+/g, " ").slice(0, 80).trim();

const mermaidNodeId = (index: number): string => `call_${index}`;

export const buildIssueCallChainGraph = (
  finding: ReviewFinding,
  issue: DebateIssue | null,
  evidenceChain: EvidenceChainStep[],
): IssueCallChainGraph | null => {
  const paths = [...contextPaths(finding, issue), ...evidencePaths(evidenceChain)]
    .map((path) => dedupe(path.map(sanitizeMermaidText).filter(Boolean)))
    .filter((path) => path.length >= 2);
  if (!paths.length) return null;
  const selected = paths.sort((a, b) => b.length - a.length)[0].slice(0, 8);
  const lines = ["flowchart LR"];
  selected.forEach((label, index) => {
    const id = mermaidNodeId(index);
    lines.push(`  ${id}["${label}"]`);
  });
  for (let index = 0; index < selected.length - 1; index += 1) {
    lines.push(`  ${mermaidNodeId(index)} --> ${mermaidNodeId(index + 1)}`);
  }
  const sourceSummary = contextPaths(finding, issue).length ? "代码上下文/图谱关系" : "证据链";
  return {
    chart: lines.join("\n"),
    sourceLabel: sourceSummary,
    summary: selected.join(" -> "),
    nodes: selected,
  };
};
