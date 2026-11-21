import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Avatar, Badge, Button, Card, Input, Modal, Popconfirm, QRCode, Space, Table, Tag, Typography } from "antd";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type Contract } from "./api/client";
import { QuotaEditor } from "./models";
import { formatTime, PageBody, PageHeader } from "./ui";

type Values = Record<string, string>;
type Step = Contract["LoginView"]["next"] | "login";

const labels: Record<string, string> = {
  username: "登录名", password: "密码", new_password: "新密码",
  current_password: "当前密码", token: "一次性令牌", code: "TOTP 验证码",
  recovery_code: "恢复码", display_name: "显示名",
};

const secret = (field: string) => field.includes("password") || field === "token" || field === "recovery_code";

export function ActionForm({ fields, submit, action, block, size }: {
  fields: string[];
  submit: string;
  action: (values: Values) => Promise<unknown>;
  block?: boolean;
  size?: "large" | "middle";
}) {
  const id = useId();
  const [code, setCode] = useState("");
  const requiresCode = fields.includes("code");
  const incompleteCode = requiresCode && code.length !== 6;
  const mutation = useMutation({ mutationFn: action });
  function finish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (incompleteCode) return;
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form)) as Values;
    if (requiresCode) data.code = code;
    mutation.mutate(data, { onSuccess: () => form.reset() });
  }
  return (
    <form onSubmit={finish} onReset={() => setCode("")} className="stack" style={{ gap: 14 }}>
      {fields.map((field) => field === "code" ? (
        <div key={field} className="stack" style={{ gap: 6 }}>
          <span id={`${id}-code-label`}>{labels.code}</span>
          <Input.OTP length={6} value={code} onInput={(digits) => setCode(digits.join(""))}
            formatter={(value) => value.replace(/\D/g, "")} inputMode="numeric" autoComplete="one-time-code"
            aria-labelledby={`${id}-code-label`} size={size} />
        </div>
      ) : (
        <label key={field} htmlFor={`${id}-${field}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span>{labels[field] ?? field}</span>
          {secret(field) ?
            <Input.Password id={`${id}-${field}`} name={field} required maxLength={128} autoComplete="off" size={size} /> :
            <Input id={`${id}-${field}`} name={field} required maxLength={80} autoComplete="off" size={size} />}
        </label>
      ))}
      {mutation.error && <Alert type="error" showIcon title={mutation.error.message} />}
      <div>
        <Button type="primary" htmlType="submit" disabled={incompleteCode} loading={mutation.isPending}
          block={block} size={size}>{submit}</Button>
      </div>
    </form>
  );
}

export function AuthFrame({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand"><span className="brand-mark">AI</span>内部 AI 平台</div>
        <Card title={title} variant="borderless" style={{ boxShadow: "0 8px 30px rgba(15,23,42,.08)" }}>
          {children}
        </Card>
      </div>
    </div>
  );
}

export function SignIn({ refresh }: { refresh: () => void }) {
  const { pathname } = useLocation();
  const [step, setStep] = useState<Step>("login");
  const [setup, setSetup] = useState<Contract["SetupView"]>();
  const [codes, setCodes] = useState<string[]>();
  const [recovery, setRecovery] = useState(false);
  const [notice, setNotice] = useState("");
  const setupMutation = useMutation({
    mutationFn: () => api<Contract["SetupView"]>("/auth/mfa/setup", "POST"),
    onSuccess: setSetup,
  });
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", "POST"),
    onSuccess: () => { setStep("login"); setSetup(undefined); setNotice("请重新登录"); },
  });

  if (pathname === "/activate" || pathname === "/reset-password") {
    const activate = pathname === "/activate";
    return (
      <AuthFrame title={activate ? "激活账号" : "重置密码"}>
        <div className="stack">
          <Typography.Text type="secondary">输入管理员提供的一次性令牌，并设置 12–128 位新密码。</Typography.Text>
          {notice && <Alert type="success" showIcon title={notice} />}
          <ActionForm key={pathname} fields={["token", activate ? "password" : "new_password"]} block size="large"
            submit={activate ? "激活" : "重置密码"} action={async (values) => {
              await api(activate ? "/auth/activate" : "/auth/reset-password", "POST", values);
              setNotice("已完成，请登录");
            }} />
          <Link to="/login">返回登录</Link>
        </div>
      </AuthFrame>
    );
  }

  const titles: Record<Step, string> = {
    login: "登录", initial_password_change: "设置新密码", mfa_setup: "绑定两步验证",
    mfa_verify: "两步验证", ready: "登录",
  };
  return (
    <AuthFrame title={step === "login" ? "登录" : `管理员安全验证 · ${titles[step]}`}>
      <div className="stack">
        {notice && <Alert type="info" showIcon title={notice} />}
        {step === "login" && <ActionForm key="login" fields={["username", "password"]} submit="登录" block size="large"
          action={async (values) => {
            const result = await api<Contract["LoginView"]>("/auth/login", "POST", values);
            if (result.next === "ready") refresh();
            else setStep(result.next);
          }} />}
        {step === "initial_password_change" && <>
          <Typography.Text type="secondary">首次登录需要修改初始密码。</Typography.Text>
          <ActionForm key="initial" fields={["new_password"]} submit="设置新密码" block
            action={async (values) => {
              await api("/auth/change-initial-password", "POST", values);
              setStep("login");
              setNotice("密码已更新，请使用新密码登录");
            }} />
        </>}
        {step === "mfa_setup" && !setup && !codes && <>
          <Typography.Text type="secondary">管理员账号需要绑定 TOTP 验证器（如 Google Authenticator）。</Typography.Text>
          <Button type="primary" block loading={setupMutation.isPending}
            onClick={() => setupMutation.mutate()}>开始绑定 TOTP</Button>
        </>}
        {setupMutation.error && <Alert type="error" showIcon title={setupMutation.error.message} />}
        {step === "mfa_setup" && setup && !codes && <>
          <Typography.Text>用验证器扫描二维码，再输入验证器显示的 6 位验证码。</Typography.Text>
          <QRCode value={setup.provisioning_uri} size={216} marginSize={4} color="#000" bgColor="#fff"
            aria-label="绑定验证器二维码" style={{ marginInline: "auto" }} />
          <details>
            <summary>无法扫码？手动输入密钥</summary>
            <Typography.Text copyable code>{setup.secret}</Typography.Text>
          </details>
          <ActionForm key="setup" fields={["code"]} submit="确认绑定" block action={async (values) => {
            const result = await api<Contract["ConfirmView"]>("/auth/mfa/confirm", "POST", values);
            setSetup(undefined);
            setCodes(result.recovery_codes);
          }} />
        </>}
        {codes && <>
          <Alert type="warning" showIcon title="请妥善保存恢复码，每个恢复码仅可使用一次" />
          <Typography.Paragraph copyable className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {codes.join("\n")}
          </Typography.Paragraph>
          <Button type="primary" block onClick={() => { setCodes(undefined); refresh(); }}>已保存，继续</Button>
        </>}
        {step === "mfa_verify" && <>
          <ActionForm key={String(recovery)} fields={[recovery ? "recovery_code" : "code"]} submit="验证" block
            action={async (values) => { await api("/auth/mfa/verify", "POST", values); refresh(); }} />
          <Button type="link" style={{ padding: 0 }} onClick={() => setRecovery(!recovery)}>
            {recovery ? "使用 TOTP" : "使用恢复码"}
          </Button>
        </>}
        {logout.error && <Alert type="error" showIcon title={logout.error.message} />}
        {step === "login" ?
          <Space separator="·"><Link to="/activate">激活账号</Link><Link to="/reset-password">重置密码</Link></Space> :
          !codes && <Button type="link" style={{ padding: 0 }} onClick={() => logout.mutate()}>清除当前登录并重新开始</Button>}
      </div>
    </AuthFrame>
  );
}

export function AccountSecurity({ refresh, admin }: { refresh: () => void; admin: boolean }) {
  const [notice, setNotice] = useState("");
  return (
    <PageBody>
      <PageHeader title="账号安全" description="管理密码与登录会话" />
      <div className="stack">
        {notice && <Alert type="success" showIcon closable={{ onClose: () => setNotice("") }} title={notice} />}
        {admin && <Card title="确认管理员身份" extra={<Typography.Text type="secondary">敏感操作前需验证</Typography.Text>}>
          <ActionForm fields={["password", "code"]} submit="确认" action={async (values) => {
            await api("/auth/reauthenticate", "POST", values); setNotice("验证有效期为 5 分钟");
          }} />
        </Card>}
        <Card title="修改密码" extra={<Typography.Text type="secondary">修改后其他登录将失效</Typography.Text>}>
          <ActionForm fields={["current_password", "new_password"]} submit="修改密码"
            action={async (values) => { await api("/auth/change-password", "POST", values); refresh(); }} />
        </Card>
        <Card title="注销其他会话">
          <ActionForm fields={["password"]} submit="注销其他会话" action={async (values) => {
            await api("/auth/revoke-other-sessions", "POST", values);
            setNotice("其他会话已注销");
          }} />
        </Card>
      </div>
    </PageBody>
  );
}

const statuses = {
  pending: { text: "待激活", status: "warning" },
  active: { text: "正常", status: "success" },
  disabled: { text: "已禁用", status: "default" },
} as const;

export function Members() {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const [cursor, setCursor] = useState<string | null>(null);
  const [issued, setIssued] = useState<string>();
  const [creating, setCreating] = useState(false);
  const [quotaUser, setQuotaUser] = useState<Contract["AdminUserView"]>();
  const list = useQuery({
    queryKey: ["admin-users", cursor],
    queryFn: () => api<Contract["MemberPage"]>(`/admin/users${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),
  });
  const change = useMutation({
    mutationFn: async ({ user, action }: { user: Contract["AdminUserView"]; action: "activate" | "reset" | "toggle" | "delete" }) => {
      const path = `/admin/users/${user.id}`;
      setIssued(undefined);
      if (action === "activate") {
        setIssued((await api<Contract["ActivationToken"]>(`${path}/activation-token`, "POST")).activation_token);
      } else if (action === "reset") {
        setIssued((await api<Contract["ResetToken"]>(`${path}/password-reset`, "POST")).reset_token);
      } else if (action === "delete") {
        await api(`${path}?revision=${user.revision}`, "DELETE");
        if (list.data?.items.length === 1) setCursor(null);
        void message.success("成员已删除");
      } else {
        const updated = await api<Contract["AdminUserView"]>(path, "PATCH", {
          status: user.status === "disabled" ? "active" : "disabled", revision: user.revision,
        });
        if (updated.status === "pending") void message.info("成员已恢复为待激活，请重发激活令牌。");
      }
    },
    onSettled: () => cache.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  return (
    <PageBody>
      <PageHeader title="成员管理" description="创建成员、发放一次性令牌、设置工作区额度与限流"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>创建成员</Button>} />
      <div className="stack">
        {issued && <Alert type="warning" showIcon closable={{ onClose: () => setIssued(undefined) }}
          title="一次性令牌（仅显示一次，请转交成员）"
          description={<Typography.Text copyable className="mono">{issued}</Typography.Text>} />}
        {(list.error || change.error) && <Alert type="error" showIcon title={(list.error || change.error)?.message} />}
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" loading={list.isFetching} pagination={false} dataSource={list.data?.items}
            scroll={{ x: 760 }} columns={[
              { title: "成员", render: (_, user) => <Space>
                <Avatar size="small" style={{ background: "#a5b4fc" }}>{user.display_name.slice(0, 1)}</Avatar>
                <span>{user.display_name}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{user.username}</Typography.Text></span>
              </Space> },
              { title: "角色", render: (_, user) => user.role === "admin" ? <Tag color="purple">管理员</Tag> : <Tag>成员</Tag> },
              { title: "状态", render: (_, user) => <Badge {...statuses[user.status]} /> },
              { title: "创建时间", render: (_, user) => formatTime(user.created_at) },
              { title: "操作", align: "right", render: (_, user) => (
                <Space size={4} wrap>
                  <Button size="small" onClick={() => setQuotaUser(user)}>额度与限流</Button>
                  {user.role === "member" && <>
                  {user.status === "pending" && <Button size="small" onClick={() => change.mutate({ user, action: "activate" })}>重发激活令牌</Button>}
                  {user.status === "active" && <Button size="small" onClick={() => change.mutate({ user, action: "reset" })}>生成重置令牌</Button>}
                  <Button size="small" disabled={change.isPending} danger={user.status !== "disabled"} onClick={() => change.mutate({ user, action: "toggle" })}>
                    {user.status === "disabled" ? "启用" : "禁用"}
                  </Button>
                  <Popconfirm title={`删除成员「${user.username}」？`}
                    description="删除后无法恢复。登录名可重新使用，历史记录保留。"
                    okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                    onConfirm={() => change.mutate({ user, action: "delete" })}>
                    <Button size="small" danger disabled={change.isPending}>删除</Button>
                  </Popconfirm>
                  </>}
                </Space>
              ) },
            ]} />
        </Card>
        <Space>
          {cursor && <Button onClick={() => setCursor(null)}>首页</Button>}
          {list.data?.next_cursor && <Button onClick={() => setCursor(list.data?.next_cursor ?? null)}>下一页</Button>}
        </Space>
      </div>
      <Modal open={creating} title="创建成员" footer={null} onCancel={() => setCreating(false)} destroyOnHidden>
        <ActionForm fields={["username", "display_name"]} submit="创建" action={async (values) => {
          setIssued(undefined);
          const result = await api<Contract["MemberCreated"]>("/admin/users", "POST", values);
          setIssued(result.activation_token);
          setCreating(false);
          await cache.invalidateQueries({ queryKey: ["admin-users"] });
        }} />
      </Modal>
      <Modal open={Boolean(quotaUser)} title={quotaUser && `${quotaUser.display_name} · 额度与限流`}
        footer={null} onCancel={() => setQuotaUser(undefined)} destroyOnHidden>
        {quotaUser && <QuotaEditor key={quotaUser.id} userId={quotaUser.id} />}
      </Modal>
    </PageBody>
  );
}
