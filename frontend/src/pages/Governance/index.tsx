import React, { useEffect, useState } from "react";
import { Card, Col, Row, Statistic, Tag, Typography } from "antd";

import { governanceApi, type GovernanceMetrics } from "@/services/api";

const { Paragraph } = Typography;

const GovernancePage: React.FC = () => {
  const [metrics, setMetrics] = useState<GovernanceMetrics | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    void governanceApi.getQualityMetrics().then(setMetrics).finally(() => setLoading(false));
  }, []);

  return (
    <div className="page-container">
      <Card className="module-card" title="治理中心" loading={loading}>
        <Paragraph>
          这一页对齐设计文档里的治理层，先提供最关键的质量指标：工具确认率、辩论存活率、人工 gate 体量和误报反馈。
        </Paragraph>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="审核任务" value={metrics?.review_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="议题总数" value={metrics?.issue_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="工具确认率" value={((metrics?.tool_confirmation_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="辩论存活率" value={((metrics?.debate_survival_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="人工 Gate" value={metrics?.needs_human_count || 0} suffix={<Tag color="error">human</Tag>} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="误报标签" value={metrics?.false_positive_count || 0} suffix={<Tag color="warning">feedback</Tag>} />
            </Card>
          </Col>
        </Row>
      </Card>
    </div>
  );
};

export default GovernancePage;
