import React from "react";
import { Card, Col, Row, Statistic, Tag } from "antd";

type OverviewCardsProps = {
  status: string;
  phase: string;
  expertCount: number;
  findingCount: number;
  issueCount: number;
  humanGateCount: number;
};

const OverviewCards: React.FC<OverviewCardsProps> = ({
  status,
  phase,
  expertCount,
  findingCount,
  issueCount,
  humanGateCount,
}) => {
  const statusLabel =
    status === "idle" ? "未开始" : status === "waiting_human" ? "待人工确认" : status;
  const phaseLabel =
    phase === "not_started" ? "尚未启动" : phase === "expert_review" ? "专家审查中" : phase;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="状态" value={statusLabel} />
        </Card>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="当前阶段" value={phaseLabel} />
        </Card>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="已启用专家" value={expertCount} />
        </Card>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="审核发现" value={findingCount} suffix={<Tag color="processing">evidence-first</Tag>} />
        </Card>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="争议议题" value={issueCount} />
        </Card>
      </Col>
      <Col xs={24} md={8} xl={4}>
        <Card className="module-card">
          <Statistic title="待人工确认" value={humanGateCount} suffix={<Tag color={humanGateCount > 0 ? "error" : "success"}>{humanGateCount > 0 ? "gate" : "clear"}</Tag>} />
        </Card>
      </Col>
    </Row>
  );
};

export default OverviewCards;
