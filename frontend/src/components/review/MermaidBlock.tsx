import React from "react";
import { Alert } from "antd";

type MermaidApi = typeof import("mermaid").default;

let mermaidPromise: Promise<MermaidApi> | null = null;
let mermaidConfigured = false;

const ensureMermaid = async (): Promise<MermaidApi> => {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((module) => module.default);
  }
  const mermaid = await mermaidPromise;
  if (!mermaidConfigured) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        primaryColor: "#f6ffed",
        primaryBorderColor: "#52c41a",
        lineColor: "#60758a",
        fontFamily: "Inter, system-ui, sans-serif",
      },
    });
    mermaidConfigured = true;
  }
  return mermaid;
};

type Props = {
  chart: string;
  className?: string;
};

const MermaidBlock: React.FC<Props> = ({ chart, className }) => {
  const [svg, setSvg] = React.useState("");
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let cancelled = false;
    const render = async () => {
      try {
        const mermaid = await ensureMermaid();
        const id = `issue-call-chain-${Math.random().toString(36).slice(2, 10)}`;
        const result = await mermaid.render(id, chart);
        if (!cancelled) {
          setSvg(result.svg);
          setError("");
        }
      } catch (err) {
        if (!cancelled) {
          setSvg("");
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    };
    void render();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (error) {
    return (
      <Alert
        type="warning"
        showIcon
        message="调用链图渲染失败"
        description={<pre className="issue-call-chain-code">{chart}</pre>}
      />
    );
  }

  return (
    <div
      className={["issue-call-chain-canvas", className].filter(Boolean).join(" ")}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
};

export default MermaidBlock;
