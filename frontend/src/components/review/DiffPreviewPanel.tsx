import React, { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";
import {
  CaretDownFilled,
  CaretRightFilled,
  FileTextOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Empty, Space, Typography } from "antd";

const { Text } = Typography;

type DiffPreviewPanelProps = {
  diff: string;
  changedFileCount?: number;
};

type DiffLine = {
  key: string;
  type: "add" | "remove" | "context" | "meta";
  oldNumber: number | null;
  newNumber: number | null;
  content: string;
};

type DiffFileSummary = {
  key: string;
  path: string;
  additions: number;
  removals: number;
  hunkCount: number;
  startIndex: number;
  endIndex: number;
};

type FileTreeNode = {
  key: string;
  label: string;
  path?: string;
  additions: number;
  removals: number;
  children: FileTreeNode[];
};

type ExpandedState = Record<string, boolean>;

type VisibleTreeNode = {
  key: string;
  node: FileTreeNode;
  depth: number;
};

const LARGE_DIFF_FILE_THRESHOLD = 400;
const LARGE_DIFF_LINE_THRESHOLD = 20000;
const DEFER_PARSE_FILE_THRESHOLD = 1000;
const DEFER_PARSE_CHAR_THRESHOLD = 1_500_000;
const TREE_RENDER_BATCH_SIZE = 240;
const MAX_ACTIVE_FILE_LINES = 1600;
const ACTIVE_FILE_HEAD_LINES = 800;
const ACTIVE_FILE_TAIL_LINES = 400;

const parseDiffFiles = (lines: string[]): DiffFileSummary[] => {
  const files: DiffFileSummary[] = [];
  let currentFile: DiffFileSummary | null = null;

  const pushCurrent = (endIndex: number) => {
    if (!currentFile) return;
    currentFile.endIndex = endIndex;
    files.push(currentFile);
    currentFile = null;
  };

  lines.forEach((rawLine, index) => {
    if (rawLine.startsWith("diff --git ")) {
      pushCurrent(index);
      const match = rawLine.match(/^diff --git a\/(.+?) b\/(.+)$/);
      currentFile = {
        key: `${files.length}-${rawLine}`,
        path: match?.[2] || match?.[1] || rawLine.replace("diff --git ", ""),
        additions: 0,
        removals: 0,
        hunkCount: 0,
        startIndex: index,
        endIndex: lines.length,
      };
      return;
    }

    if (!currentFile) return;
    if (rawLine.startsWith("@@")) {
      currentFile.hunkCount += 1;
      return;
    }
    if (rawLine.startsWith("+") && !rawLine.startsWith("+++ ")) {
      currentFile.additions += 1;
      return;
    }
    if (rawLine.startsWith("-") && !rawLine.startsWith("--- ")) {
      currentFile.removals += 1;
    }
  });

  pushCurrent(lines.length);
  return files;
};

const parseActiveFileLines = (lines: string[], file: DiffFileSummary | null): { rows: DiffLine[]; truncated: boolean } => {
  if (!file) return { rows: [], truncated: false };
  const rows: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const rawLine of lines.slice(file.startIndex, file.endIndex)) {
    if (rawLine.startsWith("diff --git ")) continue;

    if (rawLine.startsWith("@@")) {
      const match = rawLine.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldLine = Number(match?.[1] || 0);
      newLine = Number(match?.[2] || 0);
      rows.push({
        key: `${file.key}-meta-${rows.length}`,
        type: "meta",
        oldNumber: null,
        newNumber: null,
        content: rawLine,
      });
      continue;
    }

    if (
      rawLine.startsWith("index ") ||
      rawLine.startsWith("--- ") ||
      rawLine.startsWith("+++ ") ||
      rawLine.startsWith("new file mode ") ||
      rawLine.startsWith("deleted file mode ")
    ) {
      rows.push({
        key: `${file.key}-meta-${rows.length}`,
        type: "meta",
        oldNumber: null,
        newNumber: null,
        content: rawLine,
      });
      continue;
    }

    if (rawLine.startsWith("+")) {
      rows.push({
        key: `${file.key}-add-${rows.length}`,
        type: "add",
        oldNumber: null,
        newNumber: newLine,
        content: rawLine,
      });
      newLine += 1;
      continue;
    }

    if (rawLine.startsWith("-")) {
      rows.push({
        key: `${file.key}-remove-${rows.length}`,
        type: "remove",
        oldNumber: oldLine,
        newNumber: null,
        content: rawLine,
      });
      oldLine += 1;
      continue;
    }

    rows.push({
      key: `${file.key}-context-${rows.length}`,
      type: "context",
      oldNumber: oldLine || null,
      newNumber: newLine || null,
      content: rawLine.startsWith(" ") ? rawLine : ` ${rawLine}`,
    });
    if (oldLine) oldLine += 1;
    if (newLine) newLine += 1;
  }

  if (rows.length <= MAX_ACTIVE_FILE_LINES) {
    return { rows, truncated: false };
  }

  const head = rows.slice(0, ACTIVE_FILE_HEAD_LINES);
  const tail = rows.slice(-ACTIVE_FILE_TAIL_LINES);
  const omitted = rows.length - head.length - tail.length;
  return {
    rows: [
      ...head,
      {
        key: `${file.key}-omitted`,
        type: "meta",
        oldNumber: null,
        newNumber: null,
        content: `... 省略 ${omitted} 行 diff 明细，避免大文件渲染卡顿 ...`,
      },
      ...tail,
    ],
    truncated: true,
  };
};

const buildTree = (files: DiffFileSummary[]): FileTreeNode[] => {
  const roots: FileTreeNode[] = [];
  const nodeByKey = new Map<string, FileTreeNode>();

  const getOrCreateNode = (key: string, label: string): FileTreeNode => {
    const existing = nodeByKey.get(key);
    if (existing) return existing;
    const node: FileTreeNode = {
      key,
      label,
      additions: 0,
      removals: 0,
      children: [],
    };
    nodeByKey.set(key, node);
    return node;
  };

  for (const file of files) {
    const segments = file.path.split("/");
    let parent: FileTreeNode | null = null;
    let currentKey = "";

    segments.forEach((segment, index) => {
      currentKey = currentKey ? `${currentKey}/${segment}` : segment;
      const node = getOrCreateNode(currentKey, segment);
      node.additions += file.additions;
      node.removals += file.removals;

      if (index === segments.length - 1) {
        node.path = file.path;
      }

      if (!parent) {
        if (!roots.some((item) => item.key === node.key)) roots.push(node);
      } else if (!parent.children.some((item) => item.key === node.key)) {
        parent.children.push(node);
      }

      parent = node;
    });
  }

  return roots;
};

const buildInitialExpandedKeys = (nodes: FileTreeNode[], compactMode: boolean): ExpandedState => {
  const nextExpanded: ExpandedState = {};
  const walk = (items: FileTreeNode[], depth: number) => {
    for (const item of items) {
      if (!item.path) {
        nextExpanded[item.key] = compactMode ? depth < 1 : true;
        walk(item.children, depth + 1);
      }
    }
  };
  walk(nodes, 0);
  return nextExpanded;
};

const flattenVisibleTree = (
  nodes: FileTreeNode[],
  expandedKeys: ExpandedState,
  depth = 0,
): VisibleTreeNode[] => {
  const rows: VisibleTreeNode[] = [];
  for (const node of nodes) {
    rows.push({ key: node.key, node, depth });
    if (!node.path && expandedKeys[node.key] !== false && node.children.length > 0) {
      rows.push(...flattenVisibleTree(node.children, expandedKeys, depth + 1));
    }
  }
  return rows;
};

const DiffPreviewPanel: React.FC<DiffPreviewPanelProps> = ({ diff, changedFileCount }) => {
  const shouldDeferLargePreview = Boolean(
    diff &&
      ((changedFileCount || 0) >= DEFER_PARSE_FILE_THRESHOLD || diff.length >= DEFER_PARSE_CHAR_THRESHOLD),
  );
  const [previewEnabled, setPreviewEnabled] = useState(!shouldDeferLargePreview);
  const deferredDiff = useDeferredValue(diff);
  const rawLines = useMemo(
    () => (previewEnabled ? deferredDiff.replace(/\r/g, "").split("\n") : []),
    [deferredDiff, previewEnabled],
  );
  const files = useMemo(() => parseDiffFiles(rawLines), [rawLines]);
  const compactMode = files.length >= LARGE_DIFF_FILE_THRESHOLD || rawLines.length >= LARGE_DIFF_LINE_THRESHOLD;
  const tree = useMemo(() => buildTree(files), [files]);
  const [selectedPath, setSelectedPath] = useState<string | undefined>(files[0]?.path);
  const [expandedKeys, setExpandedKeys] = useState<ExpandedState>({});
  const [treeRenderLimit, setTreeRenderLimit] = useState(TREE_RENDER_BATCH_SIZE);

  const totalAdditions = useMemo(() => files.reduce((sum, file) => sum + file.additions, 0), [files]);
  const totalRemovals = useMemo(() => files.reduce((sum, file) => sum + file.removals, 0), [files]);

  useEffect(() => {
    setExpandedKeys(buildInitialExpandedKeys(tree, compactMode));
  }, [tree, compactMode]);

  useEffect(() => {
    setTreeRenderLimit(TREE_RENDER_BATCH_SIZE);
  }, [tree, compactMode, previewEnabled]);

  useEffect(() => {
    if (!files.length) {
      setSelectedPath(undefined);
      return;
    }
    if (!selectedPath || !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(files[0].path);
    }
  }, [files, selectedPath]);

  const fileByPath = useMemo(() => new Map(files.map((file) => [file.path, file])), [files]);
  const deferredSelectedPath = useDeferredValue(selectedPath);
  const activeFile = (deferredSelectedPath ? fileByPath.get(deferredSelectedPath) : undefined) || files[0] || null;
  const activeFileLines = useMemo(
    () => parseActiveFileLines(rawLines, activeFile),
    [activeFile, rawLines],
  );
  const visibleTreeRows = useMemo(
    () => flattenVisibleTree(tree, expandedKeys),
    [tree, expandedKeys],
  );
  const renderedTreeRows = useMemo(
    () => visibleTreeRows.slice(0, treeRenderLimit),
    [visibleTreeRows, treeRenderLimit],
  );
  const hiddenTreeRowCount = Math.max(visibleTreeRows.length - renderedTreeRows.length, 0);

  const toggleNode = (key: string) => {
    setExpandedKeys((current) => ({
      ...current,
      [key]: current[key] === false,
    }));
  };

  useEffect(() => {
    setPreviewEnabled(!shouldDeferLargePreview);
  }, [shouldDeferLargePreview, diff]);

  return (
    <Card className="module-card diff-panel-card" title="Diff 预览">
      {!diff ? (
        <Empty description="当前审核没有可展示的 diff。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : !previewEnabled ? (
        <Alert
          showIcon
          type="warning"
          message="当前变更规模过大，Diff 预览已切换为延迟加载模式"
          description={
            <Space direction="vertical" size={12}>
              <Text type="secondary">
                {`本次约 ${changedFileCount || "很多"} 个变更文件，统一 diff 长度约 ${diff.length.toLocaleString()} 字符。为避免页面无响应，系统默认不立即解析整份 diff。`}
              </Text>
              <Button type="primary" onClick={() => startTransition(() => setPreviewEnabled(true))}>
                手动加载 Diff 预览
              </Button>
            </Space>
          }
        />
      ) : files.length === 0 ? (
        <Empty description="当前审核没有可展示的 diff。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <>
          {compactMode ? (
            <Alert
              showIcon
              type="info"
              style={{ marginBottom: 12 }}
              message="当前变更文件很多，Diff 预览已切换为性能保护模式"
              description={`本次共 ${files.length} 个文件、${rawLines.length} 行 diff。系统已改为按需解析当前文件明细，并默认折叠目录树，避免页面卡顿。`}
            />
          ) : null}
          <div className="github-diff-shell">
            <aside className="github-diff-sidebar">
              <div className="github-diff-sidebar-header">
                <span>Changed files</span>
                <span>{files.length}</span>
              </div>
              <div className="github-diff-sidebar-summary">
                <span className="github-diff-sidebar-badge">+{totalAdditions}</span>
                <span className="github-diff-sidebar-badge is-remove">-{totalRemovals}</span>
              </div>
              <div className="github-diff-tree">
                {renderedTreeRows.map(({ key, node, depth }) => {
                  const isFile = Boolean(node.path);
                  const isActive = node.path && node.path === activeFile?.path;
                  const isExpanded = expandedKeys[node.key] !== false;
                  return (
                    <div key={key} className="github-diff-tree-node">
                      <button
                        type="button"
                        className={`github-diff-tree-item${isActive ? " is-active" : ""}${isFile ? " is-file" : " is-folder"}`}
                        style={{ paddingLeft: `${14 + depth * 16}px` }}
                        onClick={() => {
                          if (node.path) {
                            startTransition(() => setSelectedPath(node.path));
                            return;
                          }
                          toggleNode(node.key);
                        }}
                      >
                        <span className="github-diff-tree-label">
                          <span className="github-diff-tree-icon">
                            {isFile ? <FileTextOutlined /> : isExpanded ? <CaretDownFilled /> : <CaretRightFilled />}
                          </span>
                          {!isFile ? (
                            <span className="github-diff-tree-folder-icon">
                              <FolderOpenOutlined />
                            </span>
                          ) : null}
                          <span className="github-diff-tree-text">{node.label}</span>
                        </span>
                        <span className="github-diff-tree-stats">
                          {node.additions > 0 ? <span className="github-diff-stat-add">+{node.additions}</span> : null}
                          {node.removals > 0 ? <span className="github-diff-stat-remove">-{node.removals}</span> : null}
                        </span>
                      </button>
                    </div>
                  );
                })}
                {hiddenTreeRowCount > 0 ? (
                  <div className="github-diff-tree-node">
                    <Button type="link" size="small" onClick={() => setTreeRenderLimit((current) => current + TREE_RENDER_BATCH_SIZE)}>
                      继续加载目录项（剩余 {hiddenTreeRowCount} 项）
                    </Button>
                  </div>
                ) : null}
              </div>
            </aside>
            <section className="github-diff-detail">
              {!activeFile ? (
                <Empty description="请选择一个变更文件查看 diff 详情。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <div className="github-diff-file">
                  <header className="github-diff-file-header">
                    <div className="github-diff-file-meta">
                      <Text className="github-diff-file-path">{activeFile.path}</Text>
                      <div className="github-diff-file-badges">
                        <span className="github-diff-file-badge">File</span>
                        <span className="github-diff-file-badge">Hunks {activeFile.hunkCount}</span>
                        <span className="github-diff-file-badge github-diff-file-badge-add">+{activeFile.additions}</span>
                        <span className="github-diff-file-badge github-diff-file-badge-remove">-{activeFile.removals}</span>
                      </div>
                    </div>
                  </header>
                  {activeFileLines.truncated ? (
                    <Alert
                      showIcon
                      type="warning"
                      style={{ margin: "0 16px 12px" }}
                      message="当前文件 diff 很大，已截断中间部分"
                      description="为避免浏览器长时间无响应，只渲染前段和尾段 diff 明细。"
                    />
                  ) : null}
                  <div className="github-diff-file-body">
                    {activeFileLines.rows.map((line) => (
                      <div key={line.key} className={`github-diff-line github-diff-line-${line.type}`}>
                        <span className="github-diff-line-number">{line.oldNumber ?? ""}</span>
                        <span className="github-diff-line-number">{line.newNumber ?? ""}</span>
                        <code className="github-diff-line-code">{line.content || " "}</code>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </div>
        </>
      )}
    </Card>
  );
};

export default React.memo(DiffPreviewPanel);
