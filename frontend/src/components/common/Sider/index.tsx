import React, { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Form, Input, Layout, Menu, Modal, Select, Space, Tag } from "antd";
import {
  BookOutlined,
  HistoryOutlined,
  HomeOutlined,
  RobotOutlined,
  SettingOutlined,
  CodeOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";

import { projectApi, type CodeRepositorySettings, type ProjectSettings } from "@/services/api";

const { Sider: AntSider } = Layout;

type ProjectFormValues = ProjectSettings;

const emptyRepository = (): CodeRepositorySettings => ({
  repository_id: "",
  name: "",
  provider: "generic",
  clone_url: "",
  web_url_prefixes: [],
  local_path: "",
  default_branch: "main",
  enabled: true,
  auto_review_enabled: false,
  auto_review_poll_interval_seconds: 120,
  auto_sync: false,
  gitnexus_enabled: true,
  database_source_ids: [],
});

// 全局侧边导航用于在首页、审核、专家、知识和治理页面之间切换。
const AppSider: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const [collapsed, setCollapsed] = useState(false);
  const [projects, setProjects] = useState<ProjectSettings[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState("");
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [editingProject, setEditingProject] = useState<ProjectSettings | null>(null);
  const [savingProject, setSavingProject] = useState(false);
  const [projectForm] = Form.useForm<ProjectFormValues>();

  const menuItems = useMemo(
    // 菜单项固定，只需要在首次渲染时构造一次。
    () => [
      { key: "/", icon: <HomeOutlined />, label: "首页" },
      { key: "/review", icon: <CodeOutlined />, label: "检视工作台" },
      { key: "/history", icon: <HistoryOutlined />, label: "历史记录" },
      { key: "/experts", icon: <RobotOutlined />, label: "检查角色" },
      { key: "/knowledge", icon: <BookOutlined />, label: "知识库" },
      { key: "/governance", icon: <DashboardOutlined />, label: "治理中心" },
      { key: "/benchmark", icon: <ExperimentOutlined />, label: "评测中心" },
      { key: "/settings", icon: <SettingOutlined />, label: "设置" },
    ],
    [],
  );

  const selectedKey = useMemo(() => {
    // 根据当前路径推导高亮菜单项，兼容 review 详情子路由。
    if (location.pathname === "/") return "/";
    const matched = menuItems
      .filter((item) => item.key !== "/")
      .find((item) => location.pathname.startsWith(item.key));
    return matched?.key || "/";
  }, [location.pathname, menuItems]);

  const currentProject = useMemo(
    () => projects.find((item) => item.project_id === currentProjectId) || projects[0],
    [currentProjectId, projects],
  );

  const loadProjects = useCallback(async () => {
    try {
      const payload = await projectApi.list();
      setProjects(payload.projects || []);
      setCurrentProjectId(payload.default_project_id || payload.projects?.[0]?.project_id || "");
    } catch (error: any) {
      message.error(error?.message || "加载项目列表失败");
    }
  }, []);

  useEffect(() => {
    if (!projectModalOpen) return;
    const formValues: ProjectFormValues = editingProject
      ? {
          ...editingProject,
          repositories: editingProject.repositories?.length ? editingProject.repositories : [emptyRepository()],
        }
      : {
          project_id: "",
          name: "",
          description: "",
          owner_team: "",
          status: "active",
          repositories: [emptyRepository()],
        };
    projectForm.setFieldsValue(formValues);
  }, [editingProject, projectForm, projectModalOpen]);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  const openCreateProjectModal = () => {
    setEditingProject(null);
    setProjectModalOpen(true);
  };

  const openEditProjectModal = () => {
    if (!currentProject) return;
    setEditingProject(currentProject);
    setProjectModalOpen(true);
  };

  const handleSelectProject = async (projectId: string) => {
    setCurrentProjectId(projectId);
    try {
      await projectApi.setDefault(projectId);
      await loadProjects();
      window.dispatchEvent(new CustomEvent("project-changed", { detail: { projectId } }));
      message.success("已切换当前项目");
    } catch (error: any) {
      message.error(error?.message || "切换项目失败");
    }
  };

  const handleSaveProject = async () => {
    try {
      const values = await projectForm.validateFields();
      setSavingProject(true);
      const payload: ProjectSettings = {
        ...values,
        status: values.status || "active",
        repositories: (values.repositories || []).filter((repo) => String(repo.repository_id || repo.clone_url || repo.local_path || "").trim()),
      };
      if (editingProject) {
        await projectApi.update(editingProject.project_id, payload);
        window.dispatchEvent(new CustomEvent("project-changed", { detail: { projectId: editingProject.project_id } }));
        message.success("项目已更新");
      } else {
        await projectApi.create(payload);
        await projectApi.setDefault(payload.project_id);
        window.dispatchEvent(new CustomEvent("project-changed", { detail: { projectId: payload.project_id } }));
        message.success("项目已创建");
      }
      setProjectModalOpen(false);
      await loadProjects();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error?.message || "保存项目失败");
    } finally {
      setSavingProject(false);
    }
  };

  const handleDeleteProject = () => {
    if (!editingProject) return;
    if (editingProject.project_id === currentProjectId) {
      message.warning("当前项目不能直接删除，请先切换到其他项目");
      return;
    }
    Modal.confirm({
      title: "确认删除项目？",
      content: `将删除项目配置：${editingProject.name || editingProject.project_id}。历史审核数据暂不会删除。`,
      okText: "确认删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        await projectApi.remove(editingProject.project_id);
        window.dispatchEvent(new CustomEvent("project-changed"));
        message.success("项目已删除");
        setProjectModalOpen(false);
        await loadProjects();
      },
    });
  };

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
      {!collapsed && (
        <div className="app-project-switcher">
          <div className="app-sider-title">项目</div>
          <Space.Compact style={{ width: "100%" }}>
            <Select
              value={currentProject?.project_id}
              placeholder="选择项目"
              style={{ flex: 1 }}
              options={projects.map((item) => ({
                value: item.project_id,
                label: `${item.name || item.project_id}${item.status === "archived" ? "（已归档）" : ""}`,
              }))}
              onChange={handleSelectProject}
            />
            <Button icon={<PlusOutlined />} onClick={openCreateProjectModal} />
          </Space.Compact>
          {currentProject ? (
            <div className="app-project-summary">
              <div className="app-project-name">{currentProject.name || currentProject.project_id}</div>
              <div className="app-project-meta">
                {currentProject.repositories?.length || 0} 个代码仓
                {currentProject.owner_team ? ` · ${currentProject.owner_team}` : ""}
              </div>
              <Space size={6} wrap>
                <Tag color={currentProject.status === "archived" ? "default" : "processing"} style={{ margin: 0 }}>
                  {currentProject.status === "archived" ? "已归档" : "启用中"}
                </Tag>
                <Button type="link" size="small" onClick={openEditProjectModal}>
                  管理项目
                </Button>
              </Space>
            </div>
          ) : null}
        </div>
      )}
      <div className="app-sider-title">导航</div>
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
        className="app-sider-menu"
      />
      <Modal
        title={editingProject ? "编辑项目" : "新建项目"}
        open={projectModalOpen}
        width={820}
        confirmLoading={savingProject}
        okText={editingProject ? "保存项目" : "创建项目"}
        cancelText="取消"
        onOk={handleSaveProject}
        onCancel={() => setProjectModalOpen(false)}
        footer={(_, { OkBtn, CancelBtn }) => (
          <div className="project-modal-footer">
            <div>
              {editingProject ? (
                <Button danger disabled={editingProject.project_id === currentProjectId} onClick={handleDeleteProject}>
                  删除项目
                </Button>
              ) : null}
            </div>
            <Space>
              <CancelBtn />
              <OkBtn />
            </Space>
          </div>
        )}
        destroyOnHidden
      >
        <Form form={projectForm} layout="vertical" className="project-edit-form">
          <Space size={12} align="start" style={{ width: "100%" }}>
            <Form.Item
              name="project_id"
              label="项目 ID"
              rules={[{ required: true, message: "请输入项目 ID" }]}
              style={{ width: 220 }}
            >
              <Input disabled={Boolean(editingProject)} placeholder="pay-core" />
            </Form.Item>
            <Form.Item name="name" label="项目名称" rules={[{ required: true, message: "请输入项目名称" }]} style={{ width: 220 }}>
              <Input placeholder="支付中台" />
            </Form.Item>
            <Form.Item name="owner_team" label="归属团队" style={{ width: 220 }}>
              <Input placeholder="支付研发团队" />
            </Form.Item>
            <Form.Item name="status" label="状态" style={{ width: 120 }}>
              <Select
                options={[
                  { value: "active", label: "启用中" },
                  { value: "archived", label: "已归档" },
                ]}
              />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="项目说明">
            <Input.TextArea rows={2} placeholder="说明这个项目覆盖的业务范围和审核边界" />
          </Form.Item>
          <Form.List name="repositories">
            {(fields, { add, remove }) => (
              <Space direction="vertical" style={{ width: "100%" }} size={10}>
                <div className="project-repo-list-header">
                  <strong>项目绑定代码仓</strong>
                  <Button size="small" onClick={() => add(emptyRepository())}>
                    添加代码仓
                  </Button>
                </div>
                {fields.map((field) => {
                  const { key, ...restField } = field;
                  return (
                    <div className="project-repo-editor" key={key}>
                      <Space size={10} align="start" wrap>
                        <Form.Item
                          {...restField}
                          name={[field.name, "repository_id"]}
                          label="仓库 ID"
                          rules={[{ required: true, message: "请输入仓库 ID" }]}
                          style={{ width: 180 }}
                        >
                          <Input placeholder="ipc-fnd-service" />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "name"]} label="仓库名称" style={{ width: 180 }}>
                          <Input placeholder="ipc-fnd-service" />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "provider"]} label="平台" style={{ width: 130 }}>
                          <Select
                            options={[
                              { value: "codehub", label: "CodeHub" },
                              { value: "github", label: "GitHub" },
                              { value: "gitlab", label: "GitLab" },
                              { value: "generic", label: "通用" },
                            ]}
                          />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "default_branch"]} label="默认分支" style={{ width: 130 }}>
                          <Input placeholder="master" />
                        </Form.Item>
                        <Button danger style={{ marginTop: 30 }} onClick={() => remove(field.name)} disabled={fields.length <= 1}>
                          删除
                        </Button>
                      </Space>
                      <Form.Item
                        {...restField}
                        name={[field.name, "clone_url"]}
                        label="代码仓 Git 地址"
                        rules={[{ required: true, message: "请输入代码仓 Git 地址" }]}
                      >
                        <Input placeholder="https://codehub.example.com/team/repo.git" />
                      </Form.Item>
                      <Form.Item {...restField} name={[field.name, "local_path"]} label="本地代码仓目录">
                        <Input placeholder="/workspace/repo 或 d://workspace//repo" />
                      </Form.Item>
                    </div>
                  );
                })}
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>
    </AntSider>
  );
};

export default AppSider;
