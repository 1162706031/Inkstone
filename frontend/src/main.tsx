import {
  ApiOutlined, AppstoreOutlined, BarChartOutlined, CloudServerOutlined, DatabaseOutlined, KeyOutlined,
  LockOutlined, LogoutOutlined, MenuOutlined, MessageOutlined, RobotOutlined, TeamOutlined,
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import {
  App as AntApp, Avatar, Button, ConfigProvider, Drawer, Dropdown, Result, Spin,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import { lazy, type ReactNode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Contract } from "./api/client";
import { getHealth } from "./api/health";
import { AccountSecurity, AuthFrame, Members, SignIn } from "./identity";
import { AccessKeys } from "./keys";
import { ConnectionDetail, Connections, ModelDirectory, Usage } from "./models";
import { AgentDirectory, AgentEditor, Chat } from "./workbench";
import { PreviewProvider } from "./preview/catalog";
import "./styles.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { gcTime: 0 } } });
const Components = lazy(() => import("./preview/components").then((m) => ({ default: m.Components })));
const Integrations = lazy(() => import("./preview/access").then((m) => ({ default: m.Integrations })));
const WorkflowPreview = lazy(() => import("./preview/design").then((m) => ({ default: m.WorkflowPreview })));
const AgentDesign = lazy(() => import("./preview/design").then((m) => ({ default: m.AgentDesign })));

function Landing() {
  const [state, setState] = useState<"pending" | "ok" | "bad">("pending");
  const [status, setStatus] = useState("连接中");
  useEffect(() => {
    void getHealth().then(
      (health) => {
        setState(health.status === "ok" ? "ok" : "bad");
        setStatus(health.status === "ok" ? "API 已连接" : "API 状态异常");
      },
      () => { setState("bad"); setStatus("API 连接失败"); },
    );
  }, []);
  return (
    <AuthFrame>
      <div className="stack" style={{ alignItems: "center", textAlign: "center" }}>
        <span className="landing-status">
          <span className={`dot ${state === "pending" ? "" : state}`} />
          <span role="status">{status}</span>
        </span>
        <Link to="/login"><Button type="primary" size="large">登录</Button></Link>
      </div>
    </AuthFrame>
  );
}

const destinations = [
  { to: "/agents", label: "智能体列表", icon: <RobotOutlined /> },
  { to: "/chat", label: "对话", icon: <MessageOutlined /> },
  { to: "/components", label: "能力组件", icon: <AppstoreOutlined /> },
  { to: "/integrations", label: "应用接入", icon: <ApiOutlined /> },
  { to: "/access-keys", label: "接入密钥", icon: <KeyOutlined /> },
  { to: "/models", label: "模型目录", icon: <DatabaseOutlined /> },
  { to: "/usage", label: "用量", icon: <BarChartOutlined /> },
  { to: "/admin/models", label: "模型配置", icon: <CloudServerOutlined />, admin: true },
  { to: "/admin/users", label: "成员管理", icon: <TeamOutlined />, admin: true },
];

function Shell({ user, onLogout, children }: {
  user: Contract["UserView"]; onLogout: () => void; children: ReactNode;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const admin = user.role === "admin";
  const userMenu = (
    <Dropdown trigger={["click"]} placement="bottomRight" menu={{
      items: [
        { key: "security", icon: <LockOutlined />, label: "账号安全", onClick: () => navigate("/settings/security") },
        { type: "divider" },
        { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true, onClick: onLogout },
      ],
    }}>
      <button type="button" className="shell-user-btn" aria-label="账户菜单">
        <Avatar size={30} style={{ background: "var(--primary)", flex: "none" }}>{user.display_name.slice(0, 1)}</Avatar>
        <span className="shell-user-name">
          {user.display_name}
          <br /><small style={{ color: "#8a919e" }}>{admin ? "管理员" : "成员"}</small>
        </span>
      </button>
    </Dropdown>
  );
  const links = destinations.filter((item) => !item.admin || admin).map((item) =>
    <NavLink key={item.to} to={item.to} onClick={() => setOpen(false)}>
      <span aria-hidden="true">{item.icon}</span>{item.label}
    </NavLink>);
  return (
    <div className="shell">
      <header className="shell-topbar">
        <Button className="nav-toggle" type="text" icon={<MenuOutlined />} aria-label="打开导航" onClick={() => setOpen(true)} />
        <Link className="brand" to="/agents"><span className="brand-mark"><RobotOutlined /></span>内部 AI 平台</Link>
        <nav className="top-nav" aria-label="主导航">{links}</nav>
        {userMenu}
      </header>
      <Drawer title="工作台导航" open={open} onClose={() => setOpen(false)} placement="left" size={260}>
        <nav className="drawer-nav" aria-label="主导航">{links}</nav>
      </Drawer>
      <main className="shell-content">{children}</main>
    </div>
  );
}

const forbidden = <Result status="403" title="需要管理员权限" />;

function Workspace() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntApp.useApp();
  const identity = useQuery({
    queryKey: ["identity"], queryFn: async () => {
      try {
        return await api<Contract["Me"]>("/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    enabled: location.pathname !== "/", refetchOnWindowFocus: false,
  });
  const refresh = () => {
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== "identity" });
    void identity.refetch();
  };
  if (location.pathname === "/") return <Landing />;
  if (identity.isPending) return <div className="auth"><Spin size="large" aria-label="读取会话" /></div>;
  if (!identity.data) return <SignIn refresh={refresh} />;

  const { user, workspace_id: workspace } = identity.data;
  const admin = user.role === "admin";
  const logout = async () => {
    try {
      await api("/auth/logout", "POST");
      await client.cancelQueries();
      client.setQueryData(["identity"], null);
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "identity" });
      client.getMutationCache().clear();
      navigate("/login", { replace: true });
    } catch (failure) {
      message.error(failure instanceof Error ? failure.message : "退出失败");
    }
  };
  return (
    <Shell user={user} onLogout={logout}>
      <PreviewProvider key={workspace}><Suspense fallback={<Spin fullscreen tip="正在打开页面" />}><Routes>
        <Route path="/settings/security" element={<AccountSecurity refresh={refresh} admin={admin} />} />
        <Route path="/admin/users" element={admin ? <Members /> : forbidden} />
        <Route path="/admin/models" element={admin ? <Connections /> : forbidden} />
        <Route path="/admin/models/:id" element={admin ? <ConnectionDetail /> : forbidden} />
        <Route path="/agents" element={<AgentDirectory />} />
        <Route path="/agents/new" element={<AgentEditor key="new" />} />
        <Route path="/agents/:id" element={<AgentEditor />} />
        <Route path="/agents/:id/preview/:section" element={<AgentDesign />} />
        <Route path="/components" element={<Navigate to="/components/knowledge" replace />} />
        <Route path="/components/workflows" element={<WorkflowPreview />} />
        <Route path="/components/:category" element={<Components owner={user.display_name} />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/chat" element={<Chat workspace={workspace} />} />
        <Route path="/chat/:id" element={<Chat workspace={workspace} />} />
        <Route path="/access-keys" element={<AccessKeys />} />
        <Route path="/models" element={<ModelDirectory />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes></Suspense></PreviewProvider>
    </Shell>
  );
}

function App() {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }} theme={{
          token: { colorPrimary: "#2563cf", borderRadius: 8, colorBgLayout: "#f1f4f8", fontSize: 14 },
          components: {
            Card: { headerFontSize: 15 },
          },
        }}>
          <AntApp>
            <Workspace />
          </AntApp>
        </ConfigProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

const container = document.getElementById("root");
if (container) createRoot(container).render(<App />);
