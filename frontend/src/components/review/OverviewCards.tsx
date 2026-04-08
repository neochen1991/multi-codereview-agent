import React from "react";
import { Card, Col, Row, Statistic, Tag } from "antd";

type OverviewCardsProps = {
  status: string;
  phase: string;
  expertCount: number;
  findingCount: number;
  issueCount: number;
  humanGateCount: number;
  onStatusClick?: () => void;
  onPhaseClick?: () => void;
  onExpertClick?: () => void;
  onFindingClick?: () => void;
  onIssueClick?: () => void;
  onHumanGateClick?: () => void;
};

// 顶部概览卡把当前审核最核心的状态指标浓缩展示。
const OverviewCards: React.FC<OverviewCardsProps> = ({
  status,
  phase,
  expertCount,
  findingCount,
  issueCount,
  humanGateCount,
  onStatusClick,
  onPhaseClick,
  onExpertClick,
  onFindingClick,
  onIssueClick,
  onHumanGateClick,
}) => {
  const statusLabel =
    status === "idle" ? "未开始" : status === "waiting_human" ? "待人工确认" : status;
  const phaseLabel =
    phase === "not_started" ? "尚未启动" : phase === "expert_review" ? "专家审查中" : phase;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onStatusClick ? "overview-stat-button-clickable" : ""}`} onClick={onStatusClick}>
          <Card className="module-card">
            <Statistic title="状态" value={statusLabel} />
          </Card>
        </button>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onPhaseClick ? "overview-stat-button-clickable" : ""}`} onClick={onPhaseClick}>
          <Card className="module-card">
            <Statistic title="当前阶段" value={phaseLabel} />
          </Card>
        </button>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onExpertClick ? "overview-stat-button-clickable" : ""}`} onClick={onExpertClick}>
          <Card className="module-card">
            <Statistic title="本次参与专家" value={expertCount} />
          </Card>
        </button>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onFindingClick ? "overview-stat-button-clickable" : ""}`} onClick={onFindingClick}>
          <Card className="module-card">
            <Statistic title="待处理发现" value={findingCount} suffix={<Tag color="processing">evidence-first</Tag>} />
          </Card>
        </button>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onIssueClick ? "overview-stat-button-clickable" : ""}`} onClick={onIssueClick}>
          <Card className="module-card">
            <Statistic title="争议议题" value={issueCount} />
          </Card>
        </button>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <button type="button" className={`overview-stat-button ${onHumanGateClick ? "overview-stat-button-clickable" : ""}`} onClick={onHumanGateClick}>
          <Card className="module-card">
            <Statistic title="待人工确认" value={humanGateCount} suffix={<Tag color={humanGateCount > 0 ? "error" : "success"}>{humanGateCount > 0 ? "gate" : "clear"}</Tag>} />
          </Card>
        </button>
      </Col>
    </Row>
  );
};

export default OverviewCards;
