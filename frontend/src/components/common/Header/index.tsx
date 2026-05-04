import React from "react";
import { Layout, Space, Tag } from "antd";
import { GithubOutlined, RobotOutlined } from "@ant-design/icons";

const { Header: AntHeader } = Layout;

// 全局页头展示产品名称、运行标识和外部链接入口。
const AppHeader: React.FC = () => {
  return (
    <AntHeader className="app-header">
      <Space size={12} className="app-header-brand">
        <span className="app-header-logo" aria-hidden="true">
          <RobotOutlined />
        </span>
        <div className="app-header-title-wrap">
          <div className="app-header-title">代码检视协同平台</div>
          <div className="app-header-subtitle">质量检视 · 影响范围 · 人工确认</div>
        </div>
      </Space>

      <Space size={12} className="app-header-actions">
        <Tag color="processing" style={{ margin: 0 }}>
          review-ready
        </Tag>
        <a
          href="https://github.com"
          target="_blank"
          rel="noopener noreferrer"
          className="app-header-github"
          aria-label="GitHub"
        >
          <GithubOutlined />
        </a>
      </Space>
    </AntHeader>
  );
};

export default AppHeader;
