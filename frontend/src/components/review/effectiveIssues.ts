import type { DebateIssue } from "@/services/api";

const NON_EFFECTIVE_STATUSES = new Set([
  "needs_verification",
  "comment",
  "abstain",
  "rejected_after_debate",
]);

const NON_EFFECTIVE_RESOLUTIONS = new Set([
  "human_rejected",
  "needs_verification",
  "llm_judge_needs_verification",
  "targeted_debate_needs_verification",
  "feedback_profile_requires_more_evidence",
  "comment",
  "abstain",
]);

const normalizeStatusValue = (value?: string | null): string =>
  String(value || "").trim().toLowerCase();

export const isEffectiveIssue = (issue: DebateIssue): boolean => {
  if (normalizeStatusValue(issue.human_decision) === "rejected") return false;
  if (NON_EFFECTIVE_STATUSES.has(normalizeStatusValue(issue.status))) return false;
  if (NON_EFFECTIVE_RESOLUTIONS.has(normalizeStatusValue(issue.resolution))) return false;
  return true;
};

export const getEffectiveIssues = (issues: DebateIssue[]): DebateIssue[] =>
  issues.filter(isEffectiveIssue);

export const getEffectiveIssueCount = (issues: DebateIssue[]): number =>
  getEffectiveIssues(issues).length;

export const resolveDisplayedEffectiveIssueCount = ({
  issues,
  reportedIssueCount = 0,
  artifactIssueCount = 0,
}: {
  issues: DebateIssue[];
  reportedIssueCount?: number;
  artifactIssueCount?: number;
}): number => {
  const effectiveIssueCount = getEffectiveIssueCount(issues);
  if (artifactIssueCount > effectiveIssueCount && effectiveIssueCount === 0) {
    return artifactIssueCount;
  }
  return Math.max(effectiveIssueCount, reportedIssueCount);
};
