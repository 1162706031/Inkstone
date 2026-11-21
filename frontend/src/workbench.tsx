import {
  ArrowLeftOutlined, DeleteOutlined, EditOutlined, MessageOutlined, MoreOutlined,
  PlusOutlined, ReloadOutlined, RobotOutlined, SearchOutlined, SendOutlined, StopOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, App, Avatar, Button, Card, Dropdown, Empty, Form, Input, InputNumber, Modal, Popconfirm, Select, Space,
  Segmented, Spin, Tag, Tooltip, Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type Contract } from "./api/client";
import { all, ErrorAlert, errorText, formatTime, PageBody, PageHeader } from "./ui";

type Agent = Contract["AgentView"];
type Conversation = Contract["Conversation"];
type Message = Contract["Message"];
type Run = Contract["RunView"];

const defaults = {
  context_budget_tokens: 8192,
  max_output_tokens: 1024,
  context_compression_threshold_percent: 80,
  history_max_messages: 20,
};

const agentColors = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];
function AgentAvatar({ name, size = 32 }: { name: string; size?: number }) {
  const color = agentColors[[...name].reduce((n, c) => n + c.charCodeAt(0), 0) % agentColors.length];
  return <Avatar size={size} shape="square" style={{ background: color, flex: "none", borderRadius: 8 }}>
    {name.slice(0, 1) || <RobotOutlined />}
  </Avatar>;
}

async function startConversation(agent: Agent) {
  return api<Conversation>(`/agents/${agent.id}/conversations`, "POST", { agent_revision: agent.published_revision });
}

function releaseLabel(agent: Agent) {
  if (agent.status === "archived") return "已归档";
  if (!agent.published_version) return "草稿 · 未发布";
  return agent.revision === agent.published_revision
    ? `已发布 v${agent.published_version}` : `已发布 v${agent.published_version} · 有待发布修改`;
}

/* --------------------------------------------------------------------------
 * Agents
 * ------------------------------------------------------------------------ */

export function AgentDirectory() {
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState("");
  const list = useQuery({ queryKey: ["agents"], queryFn: () => all<Agent>("/agents") });
  const agents = list.data ?? [];
  const keyword = search.trim().toLowerCase();
  const visible = agents.filter((agent) => (scope === "all" || agent.is_creator) &&
    [agent.name, agent.description, agent.id].some((value) => value.toLowerCase().includes(keyword)))
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  return <PageBody wide>
    <PageHeader title="智能体管理中心" description="选择智能体查看配置并开始对话，或创建自己的文本智能体。" />
    <div className="stack">
      <div className="directory-toolbar">
        <Segmented aria-label="智能体范围" value={scope} onChange={setScope} options={[
          { label: "全部智能体", value: "all" }, { label: "我创建的", value: "mine" },
        ]} />
        <Input className="agent-search" aria-label="搜索智能体" placeholder="搜索名称、ID 或描述"
          prefix={<SearchOutlined />} allowClear value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="toolbar-actions">
          <Button icon={<ReloadOutlined />} loading={list.isFetching} onClick={() => void list.refetch()}>刷新</Button>
          <Link className="primary-link" to="/agents/new"><PlusOutlined aria-hidden />创建智能体</Link>
        </div>
      </div>
      <ErrorAlert error={list.error} />
      {list.isPending ? <Spin /> : list.data && (visible.length ? <div className="agent-grid">
        {visible.map((agent) => <Link className="agent-card" to={`/agents/${agent.id}`} key={agent.id}>
          <div className="agent-card-head"><AgentAvatar name={agent.name} size={40} />
            <Tag color={agent.visibility === "public" ? "blue" : undefined}>{agent.visibility === "public" ? "公开" : "私人"}</Tag>
          </div>
          <strong>{agent.name}</strong>
          <p>{agent.description || "暂无描述"}</p>
          <small className="agent-owner">创建人 · {agent.creator_name}</small>
          <div className="agent-card-meta">
            <span>{releaseLabel(agent)}</span><time dateTime={agent.updated_at}>{formatTime(agent.updated_at)}</time>
          </div>
        </Link>)}
      </div> : <Empty description={agents.length ? "没有符合条件的智能体" : "还没有智能体"} />)}
    </div>
  </PageBody>;
}

export function AgentEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const cache = useQueryClient();
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const models = useQuery({
    queryKey: ["available-models"],
    queryFn: () => all<Contract["AvailableModelView"]>("/models"),
  });
  const agent = useQuery({
    queryKey: ["agent", id],
    queryFn: () => api<Agent>(`/agents/${id}`),
    enabled: !!id,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const modelId = Form.useWatch("model_definition_id", form);
  const model = models.data?.find((m) => m.id === modelId);
  useEffect(() => {
    form.resetFields();
    if (agent.data) form.setFieldsValue(agent.data);
    setDirty(false);
    setConflict(false);
    setNotice("");
  }, [agent.data, form]);
  const modelName = (mid: string) => models.data?.find((m) => m.id === mid)?.display_name ?? "模型不可用";
  const fault = models.error || agent.error;
  const readOnly = agent.data?.can_edit === false;

  function reportFailure(error: unknown) {
    const stale = error instanceof ApiError && error.status === 409;
    setConflict(stale);
    setNotice(stale ? "配置已变更，请重新载入后核对修改。" : errorText(error));
  }

  async function publishDraft(current: Agent) {
    setPublishing(true);
    try {
      const saved = await api<Agent>(`/agents/${current.id}/publish`, "POST", { revision: current.revision });
      cache.setQueryData(["agent", saved.id], saved);
      void cache.invalidateQueries({ queryKey: ["agents"] });
      message.success(`已发布 v${saved.published_version}`);
    } catch (error) {
      reportFailure(error);
    } finally {
      setPublishing(false);
    }
  }

  async function archive() {
    try {
      await api(`/agents/${id}`, "DELETE");
      await cache.invalidateQueries({ queryKey: ["agents"] });
      message.success("智能体已归档");
      nav("/agents");
    } catch (e) {
      setNotice(errorText(e));
    }
  }

  return (
    <PageBody wide>
      <PageHeader back={<Link to="/agents"><ArrowLeftOutlined /> 智能体列表</Link>}
        title={id ? agent.data?.name ?? "智能体配置" : "创建智能体"}
        description={agent.data && `${releaseLabel(agent.data)} · 创建人 ${agent.data.creator_name} · 更新于 ${formatTime(agent.data.updated_at)}`} />
      <div className={readOnly || (id && !agent.data) ? "" : "agent-editor"}>
        {!readOnly && (!id || agent.data) && <nav className="section-links" aria-label="配置分区">
          <a href="#basic">基本信息</a><a href="#parameters">模型与参数</a><a href="#context">上下文</a>
          {id && <><Link to={`/agents/${id}/preview/bindings`}>资源绑定</Link>
            <Link to={`/agents/${id}/preview/access`}>访问接口</Link></>}
        </nav>}
        <div className="stack">
          {fault && <Alert type="error" showIcon title={errorText(fault)} />}
          {id && !agent.data ? (agent.isPending && <Spin />) : <Form
            layout="vertical"
            form={form}
            initialValues={{ ...defaults, visibility: "private" }}
            onValuesChange={() => setDirty(true)}
            onFinish={async (values) => {
              if (id && !agent.data?.can_edit) return;
              setSaving(true);
              try {
                const body = { ...values };
                if (id) {
                  body.revision = agent.data?.revision;
                  body.temperature ??= null;
                }
                const saved = await api<Agent>(id ? `/agents/${id}` : "/agents", id ? "PATCH" : "POST", body);
                cache.setQueryData(["agent", saved.id], saved);
                setDirty(false);
                setNotice("");
                void cache.invalidateQueries({ queryKey: ["agents"] });
                if (!id) nav(`/agents/${saved.id}`);
                message.success("草稿已保存");
              } catch (e) {
                reportFailure(e);
              } finally {
                setSaving(false);
              }
            }}
          >
            <div className="stack">
              {readOnly && agent.data ? (
                <Card title={agent.data.name} extra={<Tag>已归档</Tag>}>
                  {agent.data.description && <Typography.Paragraph>{agent.data.description}</Typography.Paragraph>}
                  <Typography.Text type="secondary">模型 · {modelName(agent.data.model_definition_id)}</Typography.Text>
                </Card>
              ) : <>
              <Card id="basic" title="基本信息"
                extra={agent.data?.can_manage && <Space>
                  <Popconfirm title="归档此智能体？" description="已有会话仍可查看。" okText="归档" onConfirm={archive}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />}>归档</Button>
                  </Popconfirm>
                </Space>}>
                <div className="form-grid">
                  <Form.Item name="name" label="名称" rules={[{ required: true }]}>
                    <Input maxLength={80} />
                  </Form.Item>
                  <Form.Item name="description" label="描述">
                    <Input maxLength={500} />
                  </Form.Item>
                  <Form.Item name="visibility" label="可见性" extra="公开后，所有成员均可编辑和发布。">
                    <Select disabled={!!id && !agent.data?.can_manage}
                      options={[{ value: "private", label: "私人" }, { value: "public", label: "公开" }]} />
                  </Form.Item>
                </div>
                <Form.Item name="system_prompt" label="系统提示词" style={{ marginBottom: 0 }}
                  extra="描述角色、目标、语气和回答格式">
                  <Input.TextArea autoSize={{ minRows: 5, maxRows: 16 }} maxLength={16000} showCount />
                </Form.Item>
              </Card>
              <Card id="parameters" title="模型与参数">
                <div className="form-grid">
                  <Form.Item name="model_definition_id" label="模型" rules={[{ required: true }]}
                    style={{ gridColumn: "1 / -1" }}>
                    <Select placeholder="选择平台模型" options={models.data?.map((m) => ({ value: m.id, label: m.display_name }))} />
                  </Form.Item>
                  <Form.Item name="temperature" label="温度"
                    extra={model && !model.supports_temperature ? "当前模型不支持温度" : "留空使用模型默认值"}>
                    <InputNumber min={0} max={2} step={0.1} disabled={model && !model.supports_temperature} />
                  </Form.Item>
                  <Form.Item name="max_output_tokens" label="输出上限"
                    extra={model ? `模型上限 ${model.max_output_tokens}` : undefined}>
                    <InputNumber min={1} max={4096} />
                  </Form.Item>
                </div>
              </Card>
              <Card id="context" title="上下文">
                <div className="form-grid">
                  <Form.Item name="context_budget_tokens" label="上下文预算"
                    extra={model ? `模型上下文 ${model.context_limit_tokens}` : "Token"}>
                    <InputNumber min={1} />
                  </Form.Item>
                  <Form.Item name="context_compression_threshold_percent" label="摘要阈值 (%)" extra="超过预算比例时压缩历史">
                    <InputNumber min={50} max={95} />
                  </Form.Item>
                  <Form.Item name="history_max_messages" label="历史消息上限" extra="保留的原始消息条数">
                    <InputNumber min={2} max={100} step={2} />
                  </Form.Item>
                </div>
              </Card>
              </>}
            </div>
            <div className="save-bar">
              {notice && <Typography.Text role="alert" type="danger">{notice}</Typography.Text>}
              {conflict && <Button onClick={() => void agent.refetch()}>重新载入</Button>}
              {agent.data?.published_version && agent.data.status === "active" && (
                <Button icon={<MessageOutlined />} onClick={async () => {
                  try {
                    const conv = await startConversation(agent.data);
                    await cache.invalidateQueries({ queryKey: ["conversations"] });
                    nav(`/chat/${conv.id}`);
                  } catch (e) {
                    setNotice(errorText(e));
                  }
                }}>
                  新建对话
                </Button>
              )}
              {!readOnly && <Button type={id ? "default" : "primary"} htmlType="submit" loading={saving} disabled={publishing || (!!id && !dirty)}>
                保存草稿
              </Button>}
              {agent.data?.can_edit && <Button type="primary" aria-label="发布" loading={publishing}
                disabled={saving || dirty || agent.data.published_revision === agent.data.revision}
                title={dirty ? "请先保存草稿" : undefined} onClick={() => agent.data && void publishDraft(agent.data)}>
                发布
              </Button>}
            </div>
          </Form>}
        </div>
      </div>
    </PageBody>
  );
}

/* --------------------------------------------------------------------------
 * Streaming
 * ------------------------------------------------------------------------ */

type Envelope = {
  schema_version: number;
  root_run_id: string;
  sequence: number;
  type: string;
  message_id?: string;
  block_id?: string;
  payload: { text?: string; content?: Message["content"]; status?: string };
};

export function applyMessageEvent(items: Message[], event: Envelope) {
  return items.map((m) => {
    if (m.id !== event.message_id) return m;
    if (event.type === "message.snapshot" && event.payload.content)
      return { ...m, content: event.payload.content, status: event.payload.status ?? m.status };
    if (event.type === "message.delta")
      return {
        ...m,
        status: "streaming",
        content: m.content.map((b) =>
          b.block_id === event.block_id ? { ...b, text: b.text + (event.payload.text ?? "") } : b,
        ),
      };
    return m;
  });
}

export async function subscribe(
  run: string,
  after: number,
  signal: AbortSignal,
  handle: (event: Envelope) => void,
) {
  const res = await fetch(`/api/v1/runs/${run}/events?after=${run}:${after}`, { signal });
  if (res.status === 410) return;
  if (!res.ok || !res.body) throw new Error(`事件连接失败 (${res.status})`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  let cursor = after;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      pending += value;
      while (pending.includes("\n\n")) {
        const end = pending.indexOf("\n\n");
        const frame = pending.slice(0, end);
        pending = pending.slice(end + 2);
        const line = frame.split("\n").find((v) => v.startsWith("data: "));
        if (!line) continue;
        const event: Envelope = JSON.parse(line.slice(6));
        if (event.root_run_id !== run || event.sequence <= cursor || event.schema_version !== 1) continue;
        cursor = event.sequence;
        handle(event);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/* --------------------------------------------------------------------------
 * Chat
 * ------------------------------------------------------------------------ */

const messageStatus: Record<string, { label: string; color?: string }> = {
  accepted: { label: "等待执行", color: "processing" },
  streaming: { label: "生成中", color: "processing" },
  cancelled: { label: "已停止", color: "warning" },
  failed: { label: "失败", color: "error" },
  interrupted: { label: "已中断", color: "error" },
};

function MessageItem({ m, compression }: { m: Message; compression?: string }) {
  const text = m.content.map((b) => b.text).join("");
  const user = m.role === "user";
  const status = messageStatus[m.status];
  const pending = !user && !text && ["accepted", "streaming"].includes(m.status);
  return (
    <div className={`msg ${user ? "msg-user" : "msg-assistant"}`}>
      {!user && <Avatar size={30} style={{ background: "#eef0ff", color: "#4f46e5", flex: "none" }} icon={<RobotOutlined />} />}
      <div className="msg-body">
        <div className="msg-bubble">
          {pending ? <span className="typing" role="img" aria-label="生成中"><i /><i /><i /></span> : text}
        </div>
        {!user && (
          <div className="msg-meta" data-status={m.status}>
            {status && <Tag color={status.color} variant="filled">{status.label}</Tag>}
            {compression === "summarized" && <Tag variant="filled">已使用摘要</Tag>}
            {compression === "truncated" && <Tag variant="filled">已裁剪历史</Tag>}
            <span>{formatTime(m.created_at)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function Transcript({ conv, refresh, onRename, onDelete }: {
  conv: Conversation; refresh: () => void; onRename: () => void; onDelete: () => void;
}) {
  const [items, setItems] = useState<Message[]>([]);
  const [active, setActive] = useState<string | null>(conv.active_run_id);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [compression, setCompression] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [generation, reload] = useState(0);
  const retry = useRef({ text: "", key: "" });
  const scroller = useRef<HTMLDivElement>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: manual reconnect starts a new snapshot and stream.
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    async function follow() {
      while (!signal.aborted) {
        try {
          const current = await api<Conversation>(`/conversations/${conv.id}`);
          let cursor: string | null = null;
          const history: Message[] = [];
          const cursors: Record<string, number> = {};
          const modes: Record<string, string> = {};
          do {
            const part: Contract["HistoryPage"] = await api(
              `/conversations/${conv.id}/messages?limit=100&cursor=${cursor ?? 0}`,
            );
            history.push(...part.items);
            Object.assign(cursors, part.run_cursors);
            Object.assign(modes, part.compression);
            cursor = part.next_cursor ?? null;
          } while (cursor && !signal.aborted);
          if (signal.aborted) return;
          setItems(history);
          setLoaded(true);
          setActive(current.active_run_id);
          setError("");
          setCompression(modes);
          if (!current.active_run_id) return;
          await subscribe(current.active_run_id, cursors[current.active_run_id] ?? 0, signal, (event) => {
            if (!signal.aborted) setItems((old) => applyMessageEvent(old, event));
          });
        } catch (e) {
          if (signal.aborted) return;
          setError(errorText(e));
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) return;
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }
    }
    void follow();
    return () => controller.abort();
  }, [conv.id, generation]);

  // Keep the newest message in view while content grows.
  // biome-ignore lint/correctness/useExhaustiveDependencies: re-run on every new message or delta.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

  async function send() {
    if (active || !text.trim() || busy) return;
    setBusy(true);
    if (retry.current.text !== text) retry.current = { text, key: crypto.randomUUID() };
    try {
      const run = await api<Run>(
        `/agents/${conv.agent_id}/runs`,
        "POST",
        { conversation_id: conv.id, input: { content: [{ type: "text", text }] } },
        { "Idempotency-Key": retry.current.key },
      );
      setText("");
      retry.current = { text: "", key: "" };
      setActive(run.id);
      refresh();
      reload((n) => n + 1);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-pane">
      <div className="chat-pane-head">
        <Space size={12} style={{ minWidth: 0 }}>
          <Link to="/chat" className="mobile-only" aria-label="返回会话列表"><Button type="text" icon={<ArrowLeftOutlined />} /></Link>
          <AgentAvatar name={conv.agent_name} size={34} />
          <div className="chat-pane-title">
            <strong>{conv.title || "新对话"}</strong>
            <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
              {conv.agent_name} · <span>{conv.release_version ? `发布版本 v${conv.release_version}` : "历史快照"}</span>
            </Typography.Text>
          </div>
        </Space>
        <Space size={4}>
          <Tooltip title="重新连接"><Button type="text" icon={<ReloadOutlined />} aria-label="重新连接"
            onClick={() => reload((n) => n + 1)} /></Tooltip>
          <Tooltip title="重命名"><Button type="text" icon={<EditOutlined />} aria-label="重命名" onClick={onRename} /></Tooltip>
          <Popconfirm title="删除此会话及其历史？" okText="确认删除" cancelText="保留" onConfirm={onDelete}>
            <Tooltip title="删除会话"><Button type="text" danger icon={<DeleteOutlined />} aria-label="删除会话" /></Tooltip>
          </Popconfirm>
        </Space>
      </div>
      <div className="chat-scroll" ref={scroller}>
        <div className="chat-thread">
          {loaded && items.length === 0 && (
            <Empty style={{ marginTop: 80 }} image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送一条消息开始对话" />
          )}
          {items.map((m) => <MessageItem key={m.id} m={m} compression={compression[m.run_id]} />)}
        </div>
      </div>
      <div className="composer-wrap">
        {error && <div className="chat-banner"><Alert type="error" showIcon title={error}
          action={<Button size="small" onClick={() => reload((n) => n + 1)}>重试</Button>} /></div>}
        <div className="composer">
          <Input.TextArea
            aria-label="消息"
            variant="borderless"
            placeholder={active ? "正在生成回复…" : "输入消息，Enter 发送，Shift+Enter 换行"}
            autoSize={{ minRows: 1, maxRows: 8 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
            maxLength={8000}
          />
          <div className="composer-bar">
            <Space size={4}>
              <span className="composer-hint">{text.length > 0 ? `${text.length} / 8000` : ""}</span>
            </Space>
            {active ? (
              <Button icon={<StopOutlined />} onClick={async () => {
                try {
                  await api(`/runs/${active}/cancel`, "POST");
                  reload((n) => n + 1);
                } catch (e) {
                  setError(errorText(e));
                }
              }}>
                停止生成
              </Button>
            ) : (
              <Button type="primary" icon={<SendOutlined aria-hidden />} loading={busy} disabled={!text.trim()} onClick={() => void send()}>
                发送
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Welcome({ agents, start }: { agents?: Agent[]; start: (a: Agent) => void }) {
  return (
    <div className="chat-empty">
      <div style={{ maxWidth: 720, width: "100%", textAlign: "center" }}>
        <Typography.Title level={3}>选择智能体开始对话</Typography.Title>
        {agents?.length === 0 ? (
          <Empty description="暂无可用的已发布智能体">
            <Link className="primary-link" to="/agents">前往智能体列表</Link>
          </Empty>
        ) : (
          <div className="form-grid" style={{ gap: 12, textAlign: "left", marginTop: 24 }}>
            {agents?.map((a) => (
              <Card key={a.id} hoverable size="small" onClick={() => start(a)}>
                <Card.Meta avatar={<AgentAvatar name={a.published_name ?? a.name} size={36} />} title={a.published_name}
                  description={<Typography.Text type="secondary" ellipsis>{a.published_description || "开始对话"}</Typography.Text>} />
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function Chat({ workspace }: { workspace: string }) {
  const { id } = useParams();
  const nav = useNavigate();
  const cache = useQueryClient();
  const history = useQuery({
    queryKey: ["conversations", workspace],
    queryFn: () => all<Conversation>("/conversations"),
  });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => all<Agent>("/agents") });
  const conv = useQuery({
    queryKey: ["conversation", id],
    queryFn: () => api<Conversation>(`/conversations/${id}`),
    enabled: !!id,
  });
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState<Conversation>();
  const [title, setTitle] = useState("");
  const storage = `pending-deletions-${workspace}`;
  const [pending, setPending] = useState<string[]>(() => JSON.parse(sessionStorage.getItem(storage) ?? "[]"));
  const agentOf = (aid: string) => agents.data?.find((a) => a.id === aid);
  const released = agents.data?.filter((a) => a.published_version);
  const chosen = released?.some((a) => a.id === selected) ? selected : released?.[0]?.id;
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["conversations"] });
    void cache.invalidateQueries({ queryKey: ["conversation", id] });
  };
  async function start(a?: Agent) {
    if (!a) return;
    try {
      const created = await startConversation(a);
      refresh();
      nav(`/chat/${created.id}`);
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function remove(cid: string) {
    try {
      await api(`/conversations/${cid}`, "DELETE");
      setError("");
      const ids = pending.filter((x) => x !== cid);
      setPending(ids);
      sessionStorage.setItem(storage, JSON.stringify(ids));
    } catch (e) {
      if (!(e instanceof ApiError && e.code === "DELETION_ARCHIVE_PENDING")) {
        setError(errorText(e));
        return;
      }
      const ids = Array.from(new Set([...pending, cid]));
      setPending(ids);
      sessionStorage.setItem(storage, JSON.stringify(ids));
    }
    refresh();
    if (id === cid) nav("/chat");
  }
  async function rename() {
    if (!renaming || !title.trim()) return;
    try {
      await api(`/conversations/${renaming.id}`, "PATCH", { title: title.trim() });
      setRenaming(undefined);
      void cache.invalidateQueries({ queryKey: ["conversations"] });
      void cache.invalidateQueries({ queryKey: ["conversation", renaming.id] });
    } catch (e) {
      setError(errorText(e));
    }
  }
  const openRename = (c: Conversation) => { setTitle(c.title); setRenaming(c); };
  const fault = error || history.error || agents.error || conv.error;
  return (
    <div className={id ? "chat chat-open" : "chat"}>
      <aside className="chat-list">
        <div className="chat-list-head">
          <Select
            aria-label="选择智能体"
            placeholder="选择智能体"
            value={chosen}
            onChange={setSelected}
            options={released?.map((a) => ({ value: a.id, label: a.published_name }))}
            notFoundContent={<Link to="/agents">前往智能体列表发布</Link>}
          />
          <Button type="primary" icon={<PlusOutlined />} disabled={!chosen} onClick={() => void start(agentOf(chosen ?? ""))}>
            新建对话
          </Button>
        </div>
        <div className="chat-list-body">
          <Typography.Text type="secondary" style={{ fontSize: 12, padding: "6px 10px", display: "block" }}>会话历史</Typography.Text>
          {history.data?.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />}
          {history.data?.map((c) => (
            <div key={c.id} className={`conv-item${c.id === id ? " active" : ""}`}>
              <Link className="conv-link" to={`/chat/${c.id}`} title={c.title} aria-label={c.title || "新对话"}>
                {c.title || "新对话"}
                <time dateTime={c.updated_at}>{formatTime(c.updated_at)}</time>
              </Link>
              <Dropdown trigger={["click"]} menu={{
                items: [
                  { key: "rename", icon: <EditOutlined />, label: "重命名", onClick: () => openRename(c) },
                  { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, onClick: () => {
                    Modal.confirm({
                      title: "删除此会话及其历史？", okText: "确认删除", cancelText: "保留", okButtonProps: { danger: true },
                      onOk: () => remove(c.id),
                    });
                  } },
                ],
              }}>
                <Button className="conv-more" type="text" size="small" icon={<MoreOutlined />} aria-label="更多操作" />
              </Dropdown>
            </div>
          ))}
        </div>
      </aside>
      <section className="chat-pane">
        {(pending.length > 0 || fault) && (
          <div style={{ padding: "12px 24px 0" }} className="stack">
            {pending.map((cid) => (
              <Alert
                key={cid}
                type="warning"
                showIcon
                title="会话已隐藏，等待归档确认"
                action={<Button size="small" onClick={() => void remove(cid)}>重试删除</Button>}
              />
            ))}
            {fault && <Alert type="error" showIcon title={errorText(error ? new Error(error) : fault)}
              closable={{ onClose: () => setError("") }} />}
          </div>
        )}
        {id && conv.data ? (
          <Transcript key={id} conv={conv.data} refresh={refresh}
            onRename={() => openRename(conv.data)} onDelete={() => void remove(id)} />
        ) : !id ? (
          <Welcome agents={released} start={(a) => void start(a)} />
        ) : null}
      </section>
      <Modal open={Boolean(renaming)} title="重命名会话" okText="保存" cancelText="取消" onOk={rename}
        okButtonProps={{ disabled: !title.trim() }} onCancel={() => setRenaming(undefined)} destroyOnHidden>
        <Input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} onPressEnter={rename} autoFocus />
      </Modal>
    </div>
  );
}
