import React, { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Form, Input, Row, Select, Space, Statistic, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";

import { reviewApi, type ReviewSummary } from "@/services/api";

const statusColor: Record<string, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
};

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();

  const loadReviews = async () => {
    setLoading(true);
    try {
      setReviews(await reviewApi.list());
    } catch (error: any) {
      message.error(error?.message || "加载审核列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadReviews();
  }, []);

  const stats = useMemo(() => {
    const total = reviews.length;
    const completed = reviews.filter((item) => item.status === "completed").length;
    const running = reviews.filter((item) => item.status === "running").length;
    return { total, completed, running };
  }, [reviews]);

  const columns: ColumnsType<ReviewSummary> = [
    { title: "Review ID", dataIndex: "review_id", key: "review_id", width: 140 },
    {
      title: "主题",
      key: "subject",
      render: (_, record) => record.subject.title || `${record.subject.source_ref} -> ${record.subject.target_ref}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: string) => <Tag color={statusColor[value] || "default"}>{value}</Tag>,
    },
    {
      title: "操作",
      key: "action",
      width: 220,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => navigate(`/review/${record.review_id}`)}>
            打开工作台
          </Button>
          <Button
            type="link"
            onClick={async () => {
              await reviewApi.start(record.review_id);
              await loadReviews();
              navigate(`/review/${record.review_id}`);
            }}
          >
            启动审核
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="home-page">
      <Card className="module-card home-hero-card">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Tag color="processing" style={{ width: "fit-content" }}>
            Expert Debate Code Review
          </Tag>
          <h2 className="home-title">多专家协同代码审核工作台</h2>
          <p className="home-subtitle">
            参考多 Agent RCA 平台的交互形式，重构为面向 MR / Branch 的专家审查、对话流和发现收敛控制台。
          </p>
        </Space>
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={8}>
          <Card className="module-card"><Statistic title="总审核数" value={stats.total} /></Card>
        </Col>
        <Col xs={24} md={8}>
          <Card className="module-card"><Statistic title="运行中" value={stats.running} /></Card>
        </Col>
        <Col xs={24} md={8}>
          <Card className="module-card"><Statistic title="已完成" value={stats.completed} /></Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={10}>
          <Card className="module-card" title="快速新建审核">
            <Form
              form={form}
              layout="vertical"
              initialValues={{
                subject_type: "mr",
                repo_id: "repo_demo",
                project_id: "proj_demo",
                target_ref: "main",
              }}
              onFinish={async (values) => {
                setSubmitting(true);
                try {
                  const created = await reviewApi.create(values);
                  message.success(`已创建审核 ${created.review_id}`);
                  form.resetFields(["title", "source_ref"]);
                  await loadReviews();
                  navigate(`/review/${created.review_id}`);
                } catch (error: any) {
                  message.error(error?.message || "创建审核失败");
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              <Form.Item label="审核类型" name="subject_type" rules={[{ required: true }]}>
                <Select options={[{ value: "mr", label: "Merge Request" }, { value: "branch", label: "Branch Compare" }]} />
              </Form.Item>
              <Form.Item label="审核标题" name="title" rules={[{ required: true }]}>
                <Input placeholder="例如：支付链路权限改造" />
              </Form.Item>
              <Form.Item label="仓库 ID" name="repo_id" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="项目 ID" name="project_id" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="源分支 / MR Ref" name="source_ref" rules={[{ required: true }]}>
                <Input placeholder="feature/..." />
              </Form.Item>
              <Form.Item label="目标分支" name="target_ref" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={submitting}>
                创建审核
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card className="module-card" title="最近审核">
            <Table rowKey="review_id" columns={columns} dataSource={reviews} loading={loading} pagination={false} />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default HomePage;
