import { humanizeReviewText, stripReviewSupplementSections } from "@/utils/displayText";

const INTERNAL_TEXT_PATTERNS: RegExp[] = [
  /baseline/i,
  /related_findings/i,
  /validator_failed/i,
  /consistency[_\s-]*validation/i,
  /Static diff signals/i,
  /issue\.issue\./i,
  /当前未记录/,
  /当前未返回/,
  /当前未识别/,
  /当前未生成/,
  /后端未返回/,
  /系统没有生成/,
  /不应直接提交/,
  /请先补齐证据/,
  /请结合审核结论补充修复方案/,
  /replace with actual patched code/i,
  /placeholder/i,
  /伪代码/,
  /占位/,
  /根据实际/,
  /请结合实际/,
];

const INTERNAL_LINE_PREFIXES = [
  "问题汇总",
  "修复建议汇总",
  "定向辩论预裁决",
  "Judge",
  "结果复核",
  "审核调度",
  "Static diff signals",
];

export const cleanUserFacingText = (value?: string | null): string => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  let text = stripReviewSupplementSections(humanizeReviewText(raw))
    .replace(/^[-*]\s*/gm, "")
    .replace(/\r\n/g, "\n")
    .trim();
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !INTERNAL_LINE_PREFIXES.some((prefix) => line.startsWith(prefix)))
    .filter((line) => !INTERNAL_TEXT_PATTERNS.some((pattern) => pattern.test(line)));
  text = lines.join("\n").trim();
  if (!text || text === "-") return "";
  if (INTERNAL_TEXT_PATTERNS.some((pattern) => pattern.test(text))) return "";
  if (/^[\[{]/.test(text)) return "";
  return text;
};

export const cleanUserFacingList = (values?: Array<string | null | undefined> | null): string[] =>
  Array.from(new Set((values || []).map((item) => cleanUserFacingText(item)).filter(Boolean)));

export const pickUserFacingText = (
  values: Array<string | null | undefined>,
  fallback = "",
): string => {
  for (const value of values) {
    const cleaned = cleanUserFacingText(value);
    if (cleaned) return cleaned;
  }
  return fallback;
};

export const isConcreteDisplayCode = (value?: string | null): boolean => {
  const code = String(value || "").trim();
  if (!code) return false;
  const lower = code.toLowerCase();
  const invalidMarkers = [
    "当前还没有生成建议修改代码",
    "当前未生成",
    "please verify against real source",
    "replace with actual patched code",
    "# suggested rewrite for",
    "todo:",
    "placeholder",
    "伪代码",
    "占位",
    "根据实际",
    "请结合实际",
    "separate validation from execution",
    "return early on invalid input",
    "keep the happy path flat and testable",
  ];
  if (invalidMarkers.some((marker) => lower.includes(marker))) return false;
  const lines = code.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length && lines.every((line) => line.startsWith("//") || line.startsWith("#") || line.startsWith("*"))) {
    return false;
  }
  return true;
};
