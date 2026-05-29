import React, { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Form, Input, Layout, Modal, Select, Space } from "antd";
import { DownOutlined, PlusOutlined, RobotOutlined, SettingOutlined } from "@ant-design/icons";

import { projectApi, type CodeRepositorySettings, type ProjectSettings } from "@/services/api";

const { Header: AntHeader } = Layout;

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

// 全局页头展示产品名称、当前项目上下文和项目管理入口。
const AppHeader: React.FC = () => {
  const { message } = AntdApp.useApp();
  const [projects, setProjects] = useState<ProjectSettings[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState("");
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [editingProject, setEditingProject] = useState<ProjectSettings | null>(null);
  const [savingProject, setSavingProject] = useState(false);
  const [projectForm] = Form.useForm<ProjectFormValues>();

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
  }, [message]);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    const handleProjectChanged = () => {
      void loadProjects();
    };
    window.addEventListener("project-changed", handleProjectChanged);
    return () => window.removeEventListener("project-changed", handleProjectChanged);
  }, [loadProjects]);

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
      window.dispatchEvent(new CustomEvent("project-changed", { detail: { projectId } }));
      window.location.reload();
    } catch (error: any) {
      await loadProjects();
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
      content: `将删除项目配置：${editingProject.name || editingProject.project_id}。历史检视数据暂不会删除。`,
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

      <Space size={10} className="app-header-actions">
        <div
          className="app-project-top-switcher"
          title={`${currentProject?.name || currentProject?.project_id || "未选择项目"} · ${currentProject?.repositories?.length || 0} 个代码仓`}
        >
          <span className="app-project-top-label">当前项目</span>
          <Select
            className="app-project-top-select"
            value={currentProject?.project_id}
            placeholder="选择项目"
            suffixIcon={<DownOutlined />}
            variant="borderless"
            popupMatchSelectWidth={320}
            options={projects.map((item) => ({
              value: item.project_id,
              label: `${item.name || item.project_id}${item.status === "archived" ? "（已归档）" : ""}`,
            }))}
            onChange={handleSelectProject}
          />
        </div>
        <Button className="app-header-project-button" icon={<SettingOutlined />} onClick={openEditProjectModal}>
          管理项目
        </Button>
        <Button className="app-header-project-add-button" icon={<PlusOutlined />} onClick={openCreateProjectModal} />
      </Space>

      <Modal
        className="project-edit-modal"
        title={editingProject ? "编辑项目" : "新建项目"}
        open={projectModalOpen}
        width="min(820px, calc(100vw - 24px))"
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
          <div className="project-basic-grid">
            <Form.Item
              name="project_id"
              label="项目 ID"
              rules={[{ required: true, message: "请输入项目 ID" }]}
            >
              <Input disabled={Boolean(editingProject)} placeholder="pay-core" />
            </Form.Item>
            <Form.Item name="name" label="项目名称" rules={[{ required: true, message: "请输入项目名称" }]}>
              <Input placeholder="支付中台" />
            </Form.Item>
            <Form.Item name="owner_team" label="归属团队">
              <Input placeholder="支付研发团队" />
            </Form.Item>
            <Form.Item name="status" label="状态">
              <Select
                options={[
                  { value: "active", label: "启用中" },
                  { value: "archived", label: "已归档" },
                ]}
              />
            </Form.Item>
          </div>
          <Form.Item name="description" label="项目说明">
            <Input.TextArea rows={2} placeholder="说明这个项目覆盖的业务范围和检视边界" />
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
                      <div className="project-repo-fields-grid">
                        <Form.Item
                          {...restField}
                          name={[field.name, "repository_id"]}
                          label="仓库 ID"
                          rules={[{ required: true, message: "请输入仓库 ID" }]}
                        >
                          <Input placeholder="ipc-fnd-service" />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "name"]} label="仓库名称">
                          <Input placeholder="ipc-fnd-service" />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "provider"]} label="平台">
                          <Select
                            options={[
                              { value: "codehub", label: "CodeHub" },
                              { value: "github", label: "GitHub" },
                              { value: "gitlab", label: "GitLab" },
                              { value: "generic", label: "通用" },
                            ]}
                          />
                        </Form.Item>
                        <Form.Item {...restField} name={[field.name, "default_branch"]} label="默认分支">
                          <Input placeholder="master" />
                        </Form.Item>
                        <Button danger className="project-repo-delete-button" onClick={() => remove(field.name)} disabled={fields.length <= 1}>
                          删除
                        </Button>
                      </div>
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
    </AntHeader>
  );
};

export default AppHeader;
