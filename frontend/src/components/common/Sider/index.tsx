import React, { useMemo, useState } from "react";
import { Layout, Menu } from "antd";
import {
  BookOutlined,
  HistoryOutlined,
  HomeOutlined,
  RobotOutlined,
  SettingOutlined,
  CodeOutlined,
  DashboardOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";

const { Sider: AntSider } = Layout;

const AppSider: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  const menuItems = useMemo(
    () => [
      { key: "/", icon: <HomeOutlined />, label: "首页" },
      { key: "/review", icon: <CodeOutlined />, label: "审核工作台" },
      { key: "/history", icon: <HistoryOutlined />, label: "历史记录" },
      { key: "/experts", icon: <RobotOutlined />, label: "专家中心" },
      { key: "/knowledge", icon: <BookOutlined />, label: "知识库" },
      { key: "/governance", icon: <DashboardOutlined />, label: "治理中心" },
      { key: "/settings", icon: <SettingOutlined />, label: "设置" },
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
    <AntSider
      className="app-sider"
      collapsible
      breakpoint="lg"
      collapsedWidth={70}
      width={224}
      collapsed={collapsed}
      onCollapse={(value) => setCollapsed(value)}
      onBreakpoint={(broken) => setCollapsed(broken)}
    >
      <div className="app-sider-title">导航</div>
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
