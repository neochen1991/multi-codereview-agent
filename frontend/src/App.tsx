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

const AppShell: React.FC = () => (
  <Layout className="app-shell">
    <AppHeader />
    <Layout hasSider className="app-main-layout">
      <AppSider />
      <Layout className="app-content-shell">
        <Content className="app-content">
          <div className="page-container">
            <Outlet />
          </div>
        </Content>
        <Footer className="app-footer">
          Multi Code Review Agent ©{new Date().getFullYear()} · 多专家协同代码审核工作台
        </Footer>
      </Layout>
    </Layout>
  </Layout>
);

const App: React.FC = () => (
  <BrowserRouter>
    <Suspense fallback={<RouteLoading />}>
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
    </Suspense>
  </BrowserRouter>
);

export default App;
