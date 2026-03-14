import React, { useEffect, useMemo, useState } from "react";
import {
  CaretDownFilled,
  CaretRightFilled,
  FileTextOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import { Card, Empty, Typography } from "antd";

const { Text } = Typography;

type DiffPreviewPanelProps = {
  diff: string;
};

type DiffLine = {
  key: string;
  type: "add" | "remove" | "context" | "meta";
  oldNumber: number | null;
  newNumber: number | null;
  content: string;
};

type DiffFile = {
  key: string;
  path: string;
  lines: DiffLine[];
  additions: number;
  removals: number;
  hunkCount: number;
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

const parseDiff = (diff: string): DiffFile[] => {
  // 先把 unified diff 解析成“文件 -> diff 行”的结构，
  // 左侧文件树和右侧详细 diff 都依赖这份中间态。
  const lines = diff.replace(/\r/g, "").split("\n");
  const files: DiffFile[] = [];
  let currentFile: DiffFile | null = null;
  let oldLine = 0;
  let newLine = 0;

  const pushCurrent = () => {
    if (currentFile && currentFile.lines.length > 0) files.push(currentFile);
  };

  for (const rawLine of lines) {
    if (rawLine.startsWith("diff --git ")) {
      pushCurrent();
      const match = rawLine.match(/^diff --git a\/(.+?) b\/(.+)$/);
      currentFile = {
        key: `${files.length}-${rawLine}`,
        path: match?.[2] || match?.[1] || rawLine.replace("diff --git ", ""),
        lines: [],
        additions: 0,
        removals: 0,
        hunkCount: 0,
      };
      oldLine = 0;
      newLine = 0;
      continue;
    }

    if (!currentFile) continue;

    if (rawLine.startsWith("@@")) {
      const match = rawLine.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldLine = Number(match?.[1] || 0);
      newLine = Number(match?.[2] || 0);
      currentFile.hunkCount += 1;
      currentFile.lines.push({
        key: `${currentFile.key}-meta-${currentFile.lines.length}`,
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
      currentFile.lines.push({
        key: `${currentFile.key}-meta-${currentFile.lines.length}`,
        type: "meta",
        oldNumber: null,
        newNumber: null,
        content: rawLine,
      });
      continue;
    }

    if (rawLine.startsWith("+")) {
      currentFile.additions += 1;
      currentFile.lines.push({
        key: `${currentFile.key}-add-${currentFile.lines.length}`,
        type: "add",
        oldNumber: null,
        newNumber: newLine,
        content: rawLine,
      });
      newLine += 1;
      continue;
    }

    if (rawLine.startsWith("-")) {
      currentFile.removals += 1;
      currentFile.lines.push({
        key: `${currentFile.key}-remove-${currentFile.lines.length}`,
        type: "remove",
        oldNumber: oldLine,
        newNumber: null,
        content: rawLine,
      });
      oldLine += 1;
      continue;
    }

    currentFile.lines.push({
      key: `${currentFile.key}-context-${currentFile.lines.length}`,
      type: "context",
      oldNumber: oldLine || null,
      newNumber: newLine || null,
      content: rawLine.startsWith(" ") ? rawLine : ` ${rawLine}`,
    });
    if (oldLine) oldLine += 1;
    if (newLine) newLine += 1;
  }

  pushCurrent();
  return files;
};

const buildTree = (files: DiffFile[]): FileTreeNode[] => {
  // 把平铺文件列表转成左侧目录树，便于复杂 PR 中按路径导航。
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

const renderTree = (
  nodes: FileTreeNode[],
  selectedPath: string | undefined,
  onSelect: (path: string) => void,
  expandedKeys: ExpandedState,
  onToggle: (key: string) => void,
  depth = 0,
): React.ReactNode =>
  nodes.map((node) => {
    const isFile = Boolean(node.path);
    const isActive = node.path && node.path === selectedPath;
    const isExpanded = expandedKeys[node.key] !== false;
    return (
      <div key={node.key} className="github-diff-tree-node">
        <button
          type="button"
          className={`github-diff-tree-item${isActive ? " is-active" : ""}${isFile ? " is-file" : " is-folder"}`}
          style={{ paddingLeft: `${14 + depth * 16}px` }}
          onClick={() => {
            if (node.path) {
              onSelect(node.path);
              return;
            }
            onToggle(node.key);
          }}
        >
          <span className="github-diff-tree-label">
            <span className="github-diff-tree-icon">
              {isFile ? (
                <FileTextOutlined />
              ) : isExpanded ? (
                <CaretDownFilled />
              ) : (
                <CaretRightFilled />
              )}
            </span>
            {!isFile ? <span className="github-diff-tree-folder-icon"><FolderOpenOutlined /></span> : null}
            <span className="github-diff-tree-text">{node.label}</span>
          </span>
          <span className="github-diff-tree-stats">
            {node.additions > 0 ? <span className="github-diff-stat-add">+{node.additions}</span> : null}
            {node.removals > 0 ? <span className="github-diff-stat-remove">-{node.removals}</span> : null}
          </span>
        </button>
        {!isFile && isExpanded && node.children.length > 0
          ? renderTree(node.children, selectedPath, onSelect, expandedKeys, onToggle, depth + 1)
          : null}
      </div>
    );
  });

const DiffPreviewPanel: React.FC<DiffPreviewPanelProps> = ({ diff }) => {
  // Diff 预览是过程页第二主视图：
  // 左侧负责“选文件”，右侧负责“看当前文件的详细 diff”。
  const files = useMemo(() => parseDiff(diff), [diff]);
  const tree = useMemo(() => buildTree(files), [files]);
  const [selectedPath, setSelectedPath] = useState<string | undefined>(files[0]?.path);
  const [expandedKeys, setExpandedKeys] = useState<ExpandedState>({});

  const totalAdditions = useMemo(() => files.reduce((sum, file) => sum + file.additions, 0), [files]);
  const totalRemovals = useMemo(() => files.reduce((sum, file) => sum + file.removals, 0), [files]);

  useEffect(() => {
    const nextExpanded: ExpandedState = {};
    const walk = (nodes: FileTreeNode[]) => {
      for (const node of nodes) {
        if (!node.path) {
          nextExpanded[node.key] = true;
          walk(node.children);
        }
      }
    };
    walk(tree);
    setExpandedKeys(nextExpanded);
  }, [tree]);

  useEffect(() => {
    if (!files.length) {
      setSelectedPath(undefined);
      return;
    }
    if (!selectedPath || !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(files[0].path);
    }
  }, [files, selectedPath]);

  const activeFile = files.find((file) => file.path === selectedPath) || files[0] || null;

  const toggleNode = (key: string) => {
    setExpandedKeys((current) => ({
      ...current,
      [key]: current[key] === false,
    }));
  };

  return (
    <Card className="module-card diff-panel-card" title="Diff 预览">
      {!diff || files.length === 0 ? (
        <Empty description="当前审核没有可展示的 diff。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
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
              {renderTree(tree, activeFile?.path, setSelectedPath, expandedKeys, toggleNode)}
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
                <div className="github-diff-file-body">
                  {activeFile.lines.map((line) => (
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
      )}
    </Card>
  );
};

export default DiffPreviewPanel;
