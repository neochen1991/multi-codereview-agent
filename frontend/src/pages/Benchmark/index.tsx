import React, { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";

import {
  reviewApi,
  type BenchmarkEvaluationPayload,
  type BenchmarkExpectedResult,
  type ReviewReport,
  type ReviewSummary,
} from "@/services/api";
import { getReviewPhaseLabel, getReviewStatusColor, getReviewStatusLabel } from "@/utils/reviewStatus";

const { Paragraph, Text } = Typography;
const { TextArea } = Input;

type BenchmarkFormValues = {
  source_review_id?: string;
  mr_url: string;
  title?: string;
  expected_findings?: string;
  evaluation_notes?: string;
  analysis_mode: "standard" | "light";
};

type BenchmarkEvaluationDraft = BenchmarkEvaluationPayload;

const BENCHMARK_TRIGGER_SOURCE = "benchmark_manual";

const isBenchmarkReview = (record: ReviewSummary) => {
  const metadata = record.subject.metadata || {};
  return metadata.trigger_source === BENCHMARK_TRIGGER_SOURCE || metadata.benchmark === true;
};

const buildReviewLabel = (record: ReviewSummary) =>
  record.subject.title || record.subject.mr_url || `${record.subject.source_ref || "-"} -> ${record.subject.target_ref || "-"}`;

const splitLines = (value?: string) =>
  String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);

const formatDateTime = (value?: string | null) => (value ? new Date(value).toLocaleString("zh-CN") : "-");

const getSeverityRank = (severity?: string) => {
  if (severity === "critical") return 4;
  if (severity === "high") return 3;
  if (severity === "medium") return 2;
  if (severity === "low") return 1;
  return 0;
};

const buildQualityDigest = (report: ReviewReport | null) => {
  if (!report) {
    return {
      issueCount: 0,
      highRiskCount: 0,
      evidenceCoverage: 0,
      verifiedCount: 0,
      impactFileCount: 0,
      impactHitCount: 0,
      testScopeCount: 0,
    };
  }
  const issues = report.issues || [];
  const issueCount = report.issue_count || issues.length;
  const evidenceIssueCount = issues.filter((item) => (item.evidence_chain || []).length > 0 || (item.evidence || []).length > 0).length;
  const highRiskCount = issues.filter((item) => getSeverityRank(item.severity) >= 3).length;
  const verifiedCount = issues.filter((item) => item.tool_verified || item.verified).length;
  return {
    issueCount,
    highRiskCount,
    evidenceCoverage: issueCount > 0 ? Math.round((evidenceIssueCount / issueCount) * 100) : 0,
    verifiedCount,
    impactFileCount: report.impact_report?.impacted_files?.length || 0,
    impactHitCount: report.impact_report?.successful_impact_targets?.length || 0,
    testScopeCount: report.impact_report?.recommended_test_scope?.length || 0,
  };
};

const defaultEvaluationDraft: BenchmarkEvaluationDraft = {
  expected_results: [],
  false_positive_issue_ids: [],
  review_quality_score: 0,
  impact_quality_score: 0,
  notes: "",
};

const normalizeVerdict = (value: unknown): BenchmarkExpectedResult["verdict"] => {
  if (value === "hit" || value === "missed" || value === "not_applicable") return value;
  return "pending";
};

const readSavedEvaluation = (metadata?: Record<string, unknown>): BenchmarkEvaluationDraft => {
  const raw = metadata?.benchmark_evaluation;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return defaultEvaluationDraft;
  }
  const record = raw as Record<string, unknown>;
  const rawExpectedResults = Array.isArray(record.expected_results) ? record.expected_results : [];
  return {
    expected_results: rawExpectedResults
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
      .map((item) => ({
        text: String(item.text || "").trim(),
        verdict: normalizeVerdict(item.verdict),
        matched_issue_ids: Array.isArray(item.matched_issue_ids)
          ? item.matched_issue_ids.map((issueId) => String(issueId || "").trim()).filter(Boolean)
          : [],
        comment: String(item.comment || "").trim(),
      }))
      .filter((item) => item.text),
    false_positive_issue_ids: Array.isArray(record.false_positive_issue_ids)
      ? record.false_positive_issue_ids.map((issueId) => String(issueId || "").trim()).filter(Boolean)
      : [],
    review_quality_score: Number(record.review_quality_score || 0),
    impact_quality_score: Number(record.impact_quality_score || 0),
    notes: String(record.notes || "").trim(),
  };
};

// 评测中心复用真实检视链路，让用户用指定 MR 观察本工具的检视质量和影响分析质量。
const BenchmarkPage: React.FC = () => {
  const navigate = useNavigate();
  const [form] = Form.useForm<BenchmarkFormValues>();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [selectedReport, setSelectedReport] = useState<ReviewReport | null>(null);
  const [selectedReviewId, setSelectedReviewId] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [savingEvaluation, setSavingEvaluation] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [evaluationDraft, setEvaluationDraft] = useState<BenchmarkEvaluationDraft>({ ...defaultEvaluationDraft });

  const loadReviews = async () => {
    setLoading(true);
    try {
      const rows = await reviewApi.list();
      setReviews(
        rows
          .slice()
          .sort(
            (left, right) =>
              new Date(right.updated_at || right.created_at || 0).getTime() -
              new Date(left.updated_at || left.created_at || 0).getTime(),
          ),
      );
      setLoadError("");
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || "加载评测任务失败";
      setLoadError(String(detail));
      message.error(String(detail));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadReviews();
  }, []);

  const benchmarkReviews = useMemo(() => reviews.filter(isBenchmarkReview), [reviews]);
  const selectableReviews = useMemo(
    () =>
      reviews
        .filter((item) => item.subject.mr_url && !isBenchmarkReview(item))
        .slice(0, 30)
        .map((item) => ({
          value: item.review_id,
          label: `${buildReviewLabel(item)} · ${item.review_id}`,
          item,
        })),
    [reviews],
  );
  const selectedBenchmark = useMemo(
    () => benchmarkReviews.find((item) => item.review_id === selectedReviewId) || benchmarkReviews[0] || null,
    [benchmarkReviews, selectedReviewId],
  );
  const expectedFindings = useMemo(() => {
    const metadata = selectedBenchmark?.subject.metadata || {};
    const rawExpected = metadata.benchmark_expected_findings;
    if (Array.isArray(rawExpected)) {
      return rawExpected.map((item) => String(item || "").trim()).filter(Boolean);
    }
    return splitLines(String(rawExpected || ""));
  }, [selectedBenchmark?.subject.metadata]);
  const digest = useMemo(() => buildQualityDigest(selectedReport), [selectedReport]);
  const issueOptions = useMemo(
    () =>
      (selectedReport?.issues || []).map((issue) => ({
        value: issue.issue_id,
        label: `${issue.title} · ${issue.file_path || "-"}:${issue.line_start || "-"}`,
      })),
    [selectedReport?.issues],
  );
  const evaluationStats = useMemo(() => {
    const expectedResults = evaluationDraft.expected_results || [];
    const hitCount = expectedResults.filter((item) => item.verdict === "hit").length;
    const missedCount = expectedResults.filter((item) => item.verdict === "missed").length;
    const pendingCount = expectedResults.filter((item) => item.verdict === "pending").length;
    const applicableCount = expectedResults.filter((item) => item.verdict !== "not_applicable").length || 1;
    return {
      hitCount,
      missedCount,
      pendingCount,
      falsePositiveCount: evaluationDraft.false_positive_issue_ids.length,
      hitRate: Math.round((hitCount / applicableCount) * 100),
    };
  }, [evaluationDraft]);

  const fillFromHistory = (reviewId: string) => {
    const option = selectableReviews.find((item) => item.value === reviewId);
    if (!option) return;
    form.setFieldsValue({
      source_review_id: reviewId,
      mr_url: option.item.subject.mr_url || "",
      title: `评测：${buildReviewLabel(option.item)}`,
    });
  };

  const createBenchmark = async (values: BenchmarkFormValues) => {
    const mrUrl = values.mr_url.trim();
    if (!mrUrl) {
      message.warning("请先填写 MR 链接");
      return;
    }
    setCreating(true);
    try {
      const expectedFindings = splitLines(values.expected_findings);
      const created = await reviewApi.create({
        subject_type: "mr",
        analysis_mode: values.analysis_mode || "standard",
        mr_url: mrUrl,
        title: values.title?.trim() || `评测：${mrUrl}`,
        metadata: {
          benchmark: true,
          trigger_source: BENCHMARK_TRIGGER_SOURCE,
          benchmark_source_review_id: values.source_review_id || "",
          benchmark_expected_findings: expectedFindings,
          benchmark_notes: values.evaluation_notes?.trim() || "",
          allow_empty_diff_fallback: false,
        },
      });
      await reviewApi.start(created.review_id);
      message.success("评测任务已启动");
      form.resetFields();
      form.setFieldValue("analysis_mode", "standard");
      setSelectedReviewId(created.review_id);
      setSelectedReport(null);
      await loadReviews();
      navigate(`/review/${created.review_id}?tab=process`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || "创建评测任务失败";
      message.error(String(detail));
    } finally {
      setCreating(false);
    }
  };

  const loadReport = async (reviewId: string) => {
    setSelectedReviewId(reviewId);
    setReportLoading(true);
    try {
      const report = await reviewApi.getReport(reviewId, { findings_limit: 200, issues_limit: 200 });
      setSelectedReport(report);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || "加载评测摘要失败";
      message.error(String(detail));
      setSelectedReport(null);
    } finally {
      setReportLoading(false);
    }
  };

  useEffect(() => {
    if (selectedBenchmark?.status === "completed" && selectedBenchmark.review_id !== selectedReport?.review_id) {
      void loadReport(selectedBenchmark.review_id);
    }
    // selectedReport 会由 loadReport 更新，这里只依赖 review_id 避免重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBenchmark?.review_id, selectedBenchmark?.status]);

  useEffect(() => {
    const saved = readSavedEvaluation(selectedBenchmark?.subject.metadata);
    const savedByText = new Map(saved.expected_results.map((item) => [item.text, item]));
    const expectedResults = expectedFindings.map((text) => ({
      text,
      verdict: savedByText.get(text)?.verdict || "pending",
      matched_issue_ids: savedByText.get(text)?.matched_issue_ids || [],
      comment: savedByText.get(text)?.comment || "",
    }));
    setEvaluationDraft({
      expected_results: expectedResults,
      false_positive_issue_ids: saved.false_positive_issue_ids,
      review_quality_score: saved.review_quality_score,
      impact_quality_score: saved.impact_quality_score,
      notes: saved.notes,
    });
  }, [expectedFindings, selectedBenchmark?.review_id, selectedBenchmark?.subject.metadata]);

  const updateExpectedResult = (index: number, patch: Partial<BenchmarkExpectedResult>) => {
    setEvaluationDraft((current) => ({
      ...current,
      expected_results: current.expected_results.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    }));
  };

  const saveEvaluation = async () => {
    if (!selectedBenchmark) {
      message.warning("请先选择评测任务");
      return;
    }
    setSavingEvaluation(true);
    try {
      const updated = await reviewApi.saveBenchmarkEvaluation(selectedBenchmark.review_id, evaluationDraft);
      setReviews((current) => current.map((item) => (item.review_id === updated.review_id ? updated : item)));
      message.success("评测结论已保存");
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || "保存评测结论失败";
      message.error(String(detail));
    } finally {
      setSavingEvaluation(false);
    }
  };

  const columns: ColumnsType<ReviewSummary> = [
    {
      title: "评测任务",
      key: "title",
      width: 360,
      render: (_, record) => (
        <Space direction="vertical" size={4} className="review-table-title-stack">
          <Text strong className="review-table-title-cell" title={buildReviewLabel(record)}>
            {buildReviewLabel(record)}
          </Text>
          <Space size={4} wrap>
            <Tag>{record.review_id}</Tag>
            <Tag color={record.analysis_mode === "light" ? "gold" : "blue"}>
              {record.analysis_mode === "light" ? "轻量模式" : "标准模式"}
            </Tag>
          </Space>
        </Space>
      ),
    },
    {
      title: "MR 链接",
      key: "mr_url",
      width: 320,
      render: (_, record) =>
        record.subject.mr_url ? (
          <a className="review-table-link-cell" href={record.subject.mr_url} target="_blank" rel="noreferrer" title={record.subject.mr_url}>
            {record.subject.mr_url}
          </a>
        ) : (
          "-"
        ),
    },
    {
      title: "阶段",
      key: "phase",
      width: 140,
      render: (_, record) => <Tag color={record.status === "running" ? "processing" : "default"}>{getReviewPhaseLabel(record.phase)}</Tag>,
    },
    {
      title: "状态",
      key: "status",
      width: 130,
      render: (_, record) => <Tag color={getReviewStatusColor(record.status)}>{getReviewStatusLabel(record.status)}</Tag>,
    },
    {
      title: "正式问题",
      dataIndex: "issue_count",
      key: "issue_count",
      width: 110,
      render: (value?: number) => Number(value || 0),
    },
    {
      title: "创建时间",
      key: "created_at",
      width: 180,
      render: (_, record) => formatDateTime(record.created_at),
    },
    {
      title: "操作",
      key: "action",
      fixed: "right",
      width: 230,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => navigate(`/review/${record.review_id}?tab=result`)}>
            看结果
          </Button>
          <Button type="link" size="small" onClick={() => navigate(`/review/${record.review_id}?tab=impact`)}>
            看影响
          </Button>
          <Button
            type="link"
            size="small"
            disabled={record.status !== "completed"}
            loading={reportLoading && selectedReviewId === record.review_id}
            onClick={() => void loadReport(record.review_id)}
          >
            评估摘要
          </Button>
        </Space>
      ),
    },
  ];

  const expectedColumns: ColumnsType<BenchmarkExpectedResult> = [
    {
      title: "预期问题",
      dataIndex: "text",
      key: "text",
      width: 280,
      render: (value: string) => <Text>{value}</Text>,
    },
    {
      title: "对照结论",
      dataIndex: "verdict",
      key: "verdict",
      width: 150,
      render: (value: BenchmarkExpectedResult["verdict"], _record, index) => (
        <Select
          size="small"
          value={value}
          style={{ width: 130 }}
          options={[
            { label: "待判断", value: "pending" },
            { label: "已命中", value: "hit" },
            { label: "漏报", value: "missed" },
            { label: "不适用", value: "not_applicable" },
          ]}
          onChange={(nextValue) => updateExpectedResult(index, { verdict: nextValue })}
        />
      ),
    },
    {
      title: "匹配正式问题",
      dataIndex: "matched_issue_ids",
      key: "matched_issue_ids",
      width: 260,
      render: (value: string[], _record, index) => (
        <Select
          mode="multiple"
          size="small"
          maxTagCount="responsive"
          value={value || []}
          placeholder="选择命中的正式问题"
          optionFilterProp="label"
          options={issueOptions}
          style={{ width: "100%" }}
          onChange={(nextValue) => updateExpectedResult(index, { matched_issue_ids: nextValue })}
        />
      ),
    },
    {
      title: "备注",
      dataIndex: "comment",
      key: "comment",
      width: 220,
      render: (value: string | undefined, _record, index) => (
        <Input
          size="small"
          value={value}
          placeholder="人工判断依据"
          onChange={(event) => updateExpectedResult(index, { comment: event.target.value })}
        />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card className="module-card" title="评测中心">
        <Paragraph>
          选择一个 MR 后会创建真实检视任务，完成后可在这里对正式问题、证据覆盖和关联影响分析进行人工评估。
        </Paragraph>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ analysis_mode: "standard" }}
          onFinish={(values) => void createBenchmark(values)}
        >
          <Row gutter={[16, 8]}>
            <Col xs={24} lg={12}>
              <Form.Item label="从历史 MR 带入" name="source_review_id">
                <Select
                  allowClear
                  showSearch
                  placeholder="可选：选择一条历史 MR"
                  optionFilterProp="label"
                  options={selectableReviews}
                  onChange={(value) => value && fillFromHistory(value)}
                />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item label="分析模式" name="analysis_mode">
                <Select
                  options={[
                    { label: "标准模式", value: "standard" },
                    { label: "轻量模式", value: "light" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} lg={14}>
              <Form.Item label="MR 链接" name="mr_url" rules={[{ required: true, message: "请填写要评测的 MR 链接" }]}>
                <Input placeholder="https://.../merge_requests/123 或 https://github.com/org/repo/pull/123" />
              </Form.Item>
            </Col>
            <Col xs={24} lg={10}>
              <Form.Item label="评测标题" name="title">
                <Input placeholder="例如：订单创建回归用例" />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item label="预期应发现的问题" name="expected_findings">
                <TextArea rows={5} placeholder="一行一个预期问题，便于评测完成后人工对照" />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item label="评测备注" name="evaluation_notes">
                <TextArea rows={5} placeholder="记录本次 MR 的背景、重点观察项或已知风险" />
              </Form.Item>
            </Col>
          </Row>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={creating}>
              创建并启动评测
            </Button>
            <Button onClick={() => void loadReviews()} loading={loading}>
              刷新评测任务
            </Button>
          </Space>
        </Form>
      </Card>

      {loadError ? <Alert type="error" showIcon message="评测任务加载失败" description={loadError} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card className="module-card" title="评估摘要" loading={reportLoading}>
            {selectedReport ? (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Space wrap>
                  <Tag color="blue">{selectedReport.review_id}</Tag>
                  <Tag color={getReviewStatusColor(selectedReport.status)}>{getReviewStatusLabel(selectedReport.status)}</Tag>
                  <Tag>{getReviewPhaseLabel(selectedReport.phase)}</Tag>
                </Space>
                <Row gutter={[12, 12]}>
                  <Col span={12}>
                    <Statistic title="正式问题" value={digest.issueCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="高风险问题" value={digest.highRiskCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="证据覆盖" value={digest.evidenceCoverage} suffix="%" />
                  </Col>
                  <Col span={12}>
                    <Statistic title="工具核验" value={digest.verifiedCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="影响文件" value={digest.impactFileCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="影响命中" value={digest.impactHitCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="建议测试项" value={digest.testScopeCount} />
                  </Col>
                  <Col span={12}>
                    <Statistic title="模型调用" value={selectedReport.llm_usage_summary?.total_calls || 0} />
                  </Col>
                </Row>
                <div className="benchmark-summary-box">
                  <Text type="secondary">人工对照重点</Text>
                  <div>{selectedReport.summary || "暂无摘要"}</div>
                </div>
                <div className="benchmark-summary-box">
                  <Text type="secondary">预期应发现的问题</Text>
                  {expectedFindings.length ? (
                    <Space direction="vertical" size={4}>
                      {expectedFindings.map((item, index) => (
                        <Text key={`${index}-${item}`}>{`${index + 1}. ${item}`}</Text>
                      ))}
                    </Space>
                  ) : (
                    <Text>本次评测未填写预期问题，可直接按结果页人工判断。</Text>
                  )}
                </div>
                <div className="benchmark-summary-box">
                  <Space wrap>
                    <Tag color={evaluationStats.hitRate >= 80 ? "success" : "warning"}>{`命中率 ${evaluationStats.hitRate}%`}</Tag>
                    <Tag color="success">{`命中 ${evaluationStats.hitCount}`}</Tag>
                    <Tag color={evaluationStats.missedCount ? "error" : "default"}>{`漏报 ${evaluationStats.missedCount}`}</Tag>
                    <Tag color={evaluationStats.falsePositiveCount ? "warning" : "default"}>{`误报 ${evaluationStats.falsePositiveCount}`}</Tag>
                    <Tag>{`待判断 ${evaluationStats.pendingCount}`}</Tag>
                  </Space>
                  {evaluationDraft.expected_results.length ? (
                    <Table
                      rowKey={(record, index) => `${index}-${record.text}`}
                      size="small"
                      columns={expectedColumns}
                      dataSource={evaluationDraft.expected_results}
                      pagination={false}
                      scroll={{ x: 910 }}
                    />
                  ) : (
                    <Alert type="info" showIcon message="未设置预期问题" description="仍可通过误报选择、质量评分和备注记录本次评测结论。" />
                  )}
                  <Space direction="vertical" size={10} style={{ width: "100%" }}>
                    <div>
                      <Text type="secondary">误报问题</Text>
                      <Select
                        mode="multiple"
                        maxTagCount="responsive"
                        value={evaluationDraft.false_positive_issue_ids}
                        placeholder="选择人工判定不成立的正式问题"
                        optionFilterProp="label"
                        options={issueOptions}
                        style={{ width: "100%", marginTop: 6 }}
                        onChange={(falsePositiveIds) =>
                          setEvaluationDraft((current) => ({ ...current, false_positive_issue_ids: falsePositiveIds }))
                        }
                      />
                    </div>
                    <Row gutter={[12, 12]}>
                      <Col span={12}>
                        <Text type="secondary">检视质量评分</Text>
                        <Select
                          value={evaluationDraft.review_quality_score}
                          style={{ width: "100%", marginTop: 6 }}
                          options={[
                            { label: "未评分", value: 0 },
                            { label: "1 分", value: 1 },
                            { label: "2 分", value: 2 },
                            { label: "3 分", value: 3 },
                            { label: "4 分", value: 4 },
                            { label: "5 分", value: 5 },
                          ]}
                          onChange={(score) => setEvaluationDraft((current) => ({ ...current, review_quality_score: score }))}
                        />
                      </Col>
                      <Col span={12}>
                        <Text type="secondary">影响分析评分</Text>
                        <Select
                          value={evaluationDraft.impact_quality_score}
                          style={{ width: "100%", marginTop: 6 }}
                          options={[
                            { label: "未评分", value: 0 },
                            { label: "1 分", value: 1 },
                            { label: "2 分", value: 2 },
                            { label: "3 分", value: 3 },
                            { label: "4 分", value: 4 },
                            { label: "5 分", value: 5 },
                          ]}
                          onChange={(score) => setEvaluationDraft((current) => ({ ...current, impact_quality_score: score }))}
                        />
                      </Col>
                    </Row>
                    <TextArea
                      rows={4}
                      value={evaluationDraft.notes}
                      placeholder="记录漏报原因、误报原因、影响分析是否可执行，以及后续要改进的规则"
                      onChange={(event) => setEvaluationDraft((current) => ({ ...current, notes: event.target.value }))}
                    />
                    <Button type="primary" onClick={() => void saveEvaluation()} loading={savingEvaluation}>
                      保存评测结论
                    </Button>
                  </Space>
                </div>
              </Space>
            ) : (
              <Empty description="选择已完成的评测任务后查看摘要" />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card className="module-card" title="评测任务列表">
            <Table
              rowKey="review_id"
              columns={columns}
              dataSource={benchmarkReviews}
              loading={loading}
              scroll={{ x: 1470 }}
              locale={{ emptyText: <Empty description="暂无评测任务" /> }}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
};

export default BenchmarkPage;
