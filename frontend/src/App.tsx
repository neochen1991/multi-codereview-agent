import React, { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { Layout, Space, Spin } from "antd";

import AppHeader from "@/components/common/Header";
import AppSider from "@/components/common/Sider";

const HomePage = lazy(() => import("@/pages/Home"));
const ReviewWorkbenchPage = lazy(() => import("@/pages/ReviewWorkbench"));
const HistoryPage = lazy(() => import("@/pages/History"));
const ExpertsPage = lazy(() => import("@/pages/Experts"));
const KnowledgePage = lazy(() => import("@/pages/Knowledge"));
const GovernancePage = lazy(() => import("@/pages/Governance"));
const SettingsPage = lazy(() => import("@/pages/Settings"));

const { Content, Footer } = Layout;

const preloadCommonRoutes = () => {
  void import("@/pages/ReviewWorkbench");
  void import("@/pages/History");
  void import("@/pages/Experts");
  void import("@/pages/Knowledge");
};

// 路由懒加载期间使用统一的加载态，避免页面闪烁。
const RouteLoading: React.FC = () => (
  <div
    style={{
      minHeight: "calc(100vh - 180px)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
    }}
  >
    <Space direction="vertical" align="center" size="middle">
      <Spin size="large" />
      <span style={{ color: "#64748b" }}>页面加载中...</span>
    </Space>
  </div>
);

// 应用外壳统一承载头部、侧边导航和内容区域。
const AppShell: React.FC = () => {
  React.useEffect(() => {
    // 首页初始化后后台预加载高频页面，减少首次切页时的等待感。
    preloadCommonRoutes();
  }, []);

  return (
    <Layout className="app-shell">
      <AppHeader />
      <Layout hasSider className="app-main-layout">
        <AppSider />
        <Layout className="app-content-shell">
          <Content className="app-content">
            <div className="page-container">
              <Suspense fallback={<RouteLoading />}>
                <Outlet />
              </Suspense>
            </div>
          </Content>
          <Footer className="app-footer">
            Multi Code Review Agent ©{new Date().getFullYear()} · 多专家协同代码审核工作台
          </Footer>
        </Layout>
      </Layout>
    </Layout>
  );
};

// App 负责装配整套路由，并把页面挂进统一 Layout。
const App: React.FC = () => (
  <BrowserRouter>
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/review" element={<ReviewWorkbenchPage />} />
        <Route path="/review/:reviewId" element={<ReviewWorkbenchPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/experts" element={<ExpertsPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/governance" element={<GovernancePage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  </BrowserRouter>
);

export default App;
