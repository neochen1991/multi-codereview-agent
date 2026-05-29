import React, { useMemo } from "react";
import { Layout, Menu } from "antd";
import {
  BookOutlined,
  HistoryOutlined,
  HomeOutlined,
  RobotOutlined,
  SettingOutlined,
  CodeOutlined,
  DashboardOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";

const { Sider: AntSider } = Layout;

const navLabel = (title: string, description: string) => (
  <span className="app-sider-menu-label">
    <span className="app-sider-menu-title">{title}</span>
    <span className="app-sider-menu-desc">{description}</span>
  </span>
);

// 侧边栏只负责导航；项目上下文统一放到顶栏右侧。
const AppSider: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const menuItems = useMemo(
    () => [
      { key: "/", icon: <HomeOutlined />, label: navLabel("首页", "待审核 MR 队列") },
      { key: "/review", icon: <CodeOutlined />, label: navLabel("检视工作台", "创建任务与看过程") },
      { key: "/history", icon: <HistoryOutlined />, label: navLabel("历史记录", "查看历史检视任务") },
      { key: "/experts", icon: <RobotOutlined />, label: navLabel("检查角色", "专家 Agent 配置") },
      { key: "/knowledge", icon: <BookOutlined />, label: navLabel("知识库", "规范文档与规则") },
      { key: "/governance", icon: <DashboardOutlined />, label: navLabel("治理中心", "阈值与质量策略") },
      { key: "/benchmark", icon: <ExperimentOutlined />, label: navLabel("评测中心", "用例评估与对比") },
      { key: "/settings", icon: <SettingOutlined />, label: navLabel("设置", "模型、Skill 与 Tool") },
    ],
    [],
  );

  const selectedKey = useMemo(() => {
    if (location.pathname === "/") return "/";
    const matched = menuItems
      .filter((item) => item.key !== "/")
      .find((item) => location.pathname.startsWith(item.key));
    return matched?.key || "/";
  }, [location.pathname, menuItems]);

  return (
    <AntSider className="app-sider" width={208} trigger={null}>
      <div className="app-sider-title">
        <span>导航</span>
        <span className="app-sider-title-hint">工作区入口</span>
      </div>
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
        className="app-sider-menu"
      />
    </AntSider>
  );
};

export default AppSider;
