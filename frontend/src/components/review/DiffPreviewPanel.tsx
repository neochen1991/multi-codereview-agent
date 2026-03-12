import React, { useMemo } from "react";
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
};

const parseDiff = (diff: string): DiffFile[] => {
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

const DiffPreviewPanel: React.FC<DiffPreviewPanelProps> = ({ diff }) => {
  const files = useMemo(() => parseDiff(diff), [diff]);

  return (
    <Card className="module-card diff-panel-card" title="Diff 预览">
      {!diff || files.length === 0 ? (
        <Empty description="当前审核没有可展示的 diff。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className="github-diff-view">
          {files.map((file) => (
            <section key={file.key} className="github-diff-file">
              <header className="github-diff-file-header">
                <Text className="github-diff-file-path">{file.path}</Text>
              </header>
              <div className="github-diff-file-body">
                {file.lines.map((line) => (
                  <div key={line.key} className={`github-diff-line github-diff-line-${line.type}`}>
                    <span className="github-diff-line-number">{line.oldNumber ?? ""}</span>
                    <span className="github-diff-line-number">{line.newNumber ?? ""}</span>
                    <code className="github-diff-line-code">{line.content || " "}</code>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
};

export default DiffPreviewPanel;
