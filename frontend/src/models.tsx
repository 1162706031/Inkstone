import { ArrowLeftOutlined, CloudDownloadOutlined, PlusOutlined, ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, App, Badge, Button, Card, Descriptions, Empty, Form, Input, InputNumber, Modal, Popconfirm, Progress,
  Select, Space, Statistic, Table, Tag, Typography,
} from "antd";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Contract } from "./api/client";
import { ErrorAlert, errorText, formatTime, type Page, PageBody, PageButtons, PageHeader, withCursor } from "./ui";

type Connection = Contract["ConnectionView"];
type Model = Contract["ModelView"];
type ModelInput = Contract["ModelCreate"];
type Quota = Contract["QuotaView"];

const fmt = (n: number) => n.toLocaleString("zh-CN");
const onOff = (status: "active" | "disabled") =>
  <Badge status={status === "active" ? "success" : "default"} text={status === "active" ? "启用" : "禁用"} />;

export function Connections() {
  const [cursor, setCursor] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<Contract["ConnectionCreate"]>();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const list = useQuery({
    queryKey: ["connections", cursor],
    queryFn: () => api<Page<Connection>>(withCursor("/admin/model-connections", cursor)),
  });
  const create = useMutation({
    mutationFn: (values: Contract["ConnectionCreate"]) =>
      api<Connection>("/admin/model-connections", "POST", values),
    onSuccess: (created) => {
      form.resetFields(); setOpen(false);
      void cache.invalidateQueries({ queryKey: ["connections"] });
      // Land on the new connection with the model import dialog open.
      navigate(`/admin/models/${created.id}?discover=1`);
    },
  });
  return <PageBody>
    <PageHeader title="模型配置管理" description="选择供应商连接，登记并测试成员可用的对话模型。"
      extra={<>
        <Button icon={<ReloadOutlined />} loading={list.isFetching} onClick={() => void list.refetch()}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新增供应商连接</Button>
      </>} />
    <div className="stack">
      <ErrorAlert error={list.error} />
      <Card styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" pagination={false} loading={list.isFetching} dataSource={list.data?.items} scroll={{ x: 640 }}
          locale={{ emptyText: <Empty description="尚未配置供应商连接" /> }} columns={[
            { title: "供应商连接", render: (_, item) => <Link to={`/admin/models/${item.id}`}>{item.name}</Link> },
            { title: "API 根地址", render: (_, item) => <Typography.Text type="secondary" className="mono">{item.base_url}</Typography.Text> },
            { title: "状态", render: (_, item) => onOff(item.status) },
            { title: "凭据版本", dataIndex: "credential_version", width: 100 },
            { title: "创建时间", render: (_, item) => formatTime(item.created_at) },
          ]} />
      </Card>
      <PageButtons cursor={cursor} next={list.data?.next_cursor} set={setCursor} />
    </div>
    <Modal open={open} title="新增供应商连接" footer={null} onCancel={() => setOpen(false)} forceRender>
      <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)}>
        <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input maxLength={80} /></Form.Item>
        <Form.Item label="API 根地址" name="base_url" rules={[{ required: true }]} extra="例如 https://api.example.com/v1">
          <Input maxLength={2048} />
        </Form.Item>
        <Form.Item label="API Key" name="api_key" rules={[{ required: true }]} extra="保存后不再显示">
          <Input.Password maxLength={8192} autoComplete="off" />
        </Form.Item>
        {create.error && <Alert type="error" showIcon title={create.error.message} style={{ marginBottom: 16 }} />}
        <Button type="primary" htmlType="submit" loading={create.isPending}>创建并拉取模型</Button>
      </Form>
    </Modal>
  </PageBody>;
}

function ModelForm({ initial, submit, pending, onFinish }: {
  initial?: Model; submit: string; pending: boolean; onFinish: (model: ModelInput & { status?: Model["status"] }) => void;
}) {
  return <Form layout="vertical" initialValues={initial ?? {
    context_limit_tokens: 8192, max_output_tokens: 1024, supports_temperature: false, usage_support: "unknown",
  }} onFinish={onFinish}>
    <Form.Item label="上游模型标识" name="upstream_model" rules={[{ required: true }]}><Input maxLength={200} /></Form.Item>
    <Form.Item label="显示名称" name="display_name" rules={[{ required: true }]}><Input maxLength={80} /></Form.Item>
    <div className="form-grid">
      <Form.Item label="上下文 Token" name="context_limit_tokens" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item>
      <Form.Item label="输出 Token" name="max_output_tokens" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item>
      <Form.Item label="温度" name="supports_temperature" rules={[{ required: true }]}>
        <Select options={[{ value: true, label: "支持" }, { value: false, label: "不支持" }]} />
      </Form.Item>
      {initial ? (
        <Form.Item label="状态" name="status">
          <Select options={[{ value: "active", label: "启用" }, { value: "disabled", label: "禁用" }]} />
        </Form.Item>
      ) : (
        <Form.Item label="用量支持" name="usage_support">
          <Select options={[{ value: "unknown", label: "未知" }, { value: "supported", label: "支持" },
            { value: "unsupported", label: "不支持" }]} />
        </Form.Item>
      )}
    </div>
    <Button type="primary" htmlType="submit" loading={pending}>{submit}</Button>
  </Form>;
}

/** Pull the provider's `/models` list and register the selected ones in one go. */
function DiscoverModal({ connectionId, open, onClose, onImported }: {
  connectionId: string; open: boolean; onClose: () => void; onImported: (created: number) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const query = useQuery({
    queryKey: ["discover", connectionId],
    queryFn: () => api<Contract["Discovery"]>(`/admin/model-connections/${connectionId}/discover`),
    enabled: open,
    staleTime: 0,
  });
  const items = query.data?.items ?? [];
  const fresh = items.filter((m) => !m.registered);
  const shown = items.filter((m) => m.upstream_model.toLowerCase().includes(filter.trim().toLowerCase()));
  const save = useMutation({
    mutationFn: () => api<Contract["ImportResult"]>(`/admin/model-connections/${connectionId}/models/import`, "POST", {
      models: items.filter((m) => selected.includes(m.upstream_model)).map((m) => ({
        upstream_model: m.upstream_model, context_limit_tokens: m.context_limit_tokens,
      })),
    }),
    onSuccess: (result) => { setSelected([]); onImported(result.created); },
  });
  return (
    <Modal open={open} title="从供应商拉取模型" width={680} onCancel={onClose} destroyOnHidden
      okText={selected.length ? `导入 ${selected.length} 个模型` : "导入"} cancelText="关闭"
      okButtonProps={{ disabled: !selected.length, loading: save.isPending }} onOk={() => save.mutate()}>
      <div className="stack" style={{ gap: 12 }}>
        <Typography.Text type="secondary">
          读取供应商 <span className="mono">/models</span> 接口。导入后按默认参数登记（未知上下文按 8192），
          可在列表中再编辑；模型需测试通过后才对成员可用。
        </Typography.Text>
        <ErrorAlert error={query.error || save.error} />
        <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
          <Input.Search allowClear placeholder="筛选模型" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 240 }} />
          <Space>
            <Button size="small" disabled={!fresh.length}
              onClick={() => setSelected(fresh.filter((m) => shown.includes(m)).map((m) => m.upstream_model))}>全选未登记</Button>
            <Button size="small" disabled={!selected.length} onClick={() => setSelected([])}>清空</Button>
            <Button size="small" icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button>
          </Space>
        </Space>
        <Table rowKey="upstream_model" size="small" pagination={false} loading={query.isFetching} dataSource={shown}
          scroll={{ y: 360 }} locale={{ emptyText: <Empty description={query.isFetching ? "正在读取…" : "供应商未返回模型"} /> }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => setSelected(keys as string[]),
            getCheckboxProps: (m) => ({ disabled: m.registered }),
          }}
          columns={[
            { title: "上游模型标识", render: (_, m) => <span className="mono">{m.upstream_model}</span> },
            { title: "上下文", width: 120, render: (_, m) => m.context_limit_tokens ? fmt(m.context_limit_tokens) : <Typography.Text type="secondary">未知</Typography.Text> },
            { title: "", width: 80, render: (_, m) => m.registered && <Tag>已登记</Tag> },
          ]} />
      </div>
    </Modal>
  );
}

const testStatus = {
  passed: <Tag color="success">通过</Tag>,
  failed: <Tag color="error">失败</Tag>,
  untested: <Tag>待测试</Tag>,
};

const testErrors: Record<string, string> = {
  ENDPOINT_FORBIDDEN: "地址被安全策略拦截，需加入 PRIVATE_MODEL_ALLOWLIST",
  ENDPOINT_UNAVAILABLE: "无法连接供应商地址",
  INVALID_ENDPOINT: "API 根地址格式无效",
  UPSTREAM_UNAUTHORIZED: "API Key 被拒绝",
  UPSTREAM_REJECTED: "供应商拒绝了请求（模型名、权限或限流）",
  UPSTREAM_PROTOCOL: "返回内容不是 OpenAI 兼容的流式格式",
  UPSTREAM_TIMEOUT: "供应商响应超时",
  UPSTREAM_INTERRUPTED: "供应商未返回内容或中途断开",
  UPSTREAM_RESPONSE_LIMIT: "返回内容过大",
  MONTHLY_QUOTA_EXCEEDED: "本月额度已用完",
  STRICT_BUDGET_UNSUPPORTED: "严格额度模式下该模型不可用",
  MODEL_ADMISSION_LIMIT: "并发调用过多，稍后再试",
  MODEL_CONFIGURATION_CHANGED: "测试期间模型配置被修改",
  CONTEXT_LIMIT: "上下文上限过小",
  RUN_TIMEOUT: "测试超时",
  QUEUE_TIMEOUT: "排队超时，worker 可能未运行",
  REDIS_UNAVAILABLE: "Redis 不可用",
  DEPENDENCY_UNAVAILABLE: "后端依赖不可用",
};

/** A test run the backend is still working on (queued in the worker or streaming from the provider). */
export const testPending = (m: Pick<Model, "test_run_status">) =>
  m.test_run_status === "queued" || m.test_run_status === "running";

/**
 * Test status for one model. State comes from the server (the model's latest test run), so it
 * survives navigation and reloads; the page polls the model list while any test is pending.
 */
function TestCell({ model, start, stop, starting, enabled }: {
  model: Model; start: () => void; stop: () => void; starting: boolean; enabled: boolean;
}) {
  const waiting = testPending(model) || starting;
  const queuedFor = model.test_requested_at ? (Date.now() - new Date(model.test_requested_at).getTime()) / 1000 : 0;
  const failure = !waiting && model.test_error ? testErrors[model.test_error] ?? model.test_error : undefined;
  const tag = waiting
    ? <Tag icon={<SyncOutlined spin />} color="processing">{model.test_run_status === "running" ? "测试中" : "排队中"}</Tag>
    : testStatus[model.last_test_status];
  return (
    <Space orientation="vertical" size={2}>
      <Space size={4}>
        {tag}
        {waiting ? (
          <Button size="small" type="link" danger style={{ padding: 0 }} disabled={starting || !model.test_run_id} onClick={stop}>
            终止
          </Button>
        ) : (
          <Button size="small" type="link" style={{ padding: 0 }} disabled={!enabled} onClick={start}>
            {model.last_test_status === "untested" && !model.test_run_status ? "测试" : "重新测试"}
          </Button>
        )}
      </Space>
      {model.test_run_status === "queued" && queuedFor > 10 &&
        <Typography.Text type="warning" style={{ fontSize: 12 }}>
          worker 未领取任务，请在 platform/backend 运行 uv run --locked python -m ai_platform.worker
        </Typography.Text>}
      {failure && <Typography.Text type="danger" style={{ fontSize: 12 }}>{failure}</Typography.Text>}
    </Space>
  );
}

export function ConnectionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const path = `/admin/model-connections/${id}`;
  const cache = useQueryClient();
  const [cursor, setCursor] = useState<string | null>(null);
  const [editing, setEditing] = useState<Model | "new">();
  const [params, setParams] = useSearchParams();
  const { message } = App.useApp();
  const [discovering, setDiscovering] = useState(params.get("discover") === "1");
  const closeDiscover = () => {
    setDiscovering(false);
    if (params.has("discover")) setParams({}, { replace: true });
  };
  const [form] = Form.useForm<Required<Pick<Contract["ConnectionPatch"], "name" | "status">> & { api_key?: string }>();
  const connection = useQuery({ queryKey: ["connection", id], queryFn: () => api<Connection>(path) });
  const models = useQuery({
    queryKey: ["connection-models", id, cursor],
    queryFn: () => api<Page<Model>>(withCursor(`${path}/models`, cursor)),
    refetchInterval: ({ state }) => (state.data?.items.some(testPending) ? 1500 : false),
  });
  const refreshModels = () => cache.invalidateQueries({ queryKey: ["connection-models", id] });
  const [starting, setStarting] = useState<string[]>([]);
  const startTest = async (modelIds: string[]) => {
    setStarting((s) => [...s, ...modelIds]);
    // Fire all requests at once; the worker runs up to 4 tests in parallel.
    const results = await Promise.allSettled(modelIds.map((mid) => api(`${path}/test`, "POST",
      { model_definition_id: mid }, { "Idempotency-Key": crypto.randomUUID() })));
    const failed = results.filter((r) => r.status === "rejected") as PromiseRejectedResult[];
    if (failed.length) message.error(`${failed.length} 个测试未能启动：${errorText(failed[0].reason)}`);
    await refreshModels();
    setStarting((s) => s.filter((x) => !modelIds.includes(x)));
  };
  const stopTest = async (runId?: string | null) => {
    if (!runId) return;
    try {
      await api(`/runs/${runId}/cancel`, "POST");
    } catch (e) {
      message.error(errorText(e));
    }
    await refreshModels();
  };
  const untested = (models.data?.items ?? []).filter((m) =>
    m.status === "active" && m.last_test_status !== "passed" && !testPending(m));
  const save = useMutation({
    mutationFn: (values: Contract["ConnectionPatch"]) => api<Connection>(path, "PATCH", values),
    onSuccess: (updated) => {
      cache.setQueryData(["connection", id], updated); form.setFieldValue("api_key", undefined);
      void refreshModels();
    },
  });
  const remove = useMutation({
    mutationFn: () => api(path, "DELETE"),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
      navigate("/admin/models");
    },
  });
  const upsert = useMutation({
    mutationFn: ({ model, values }: { model?: Model; values: ModelInput & { status?: Model["status"] } }) =>
      model ? api(`/admin/models/${model.id}`, "PATCH", { ...values, revision: model.revision }) :
        api(`${path}/models`, "POST", values),
    onSuccess: () => { setEditing(undefined); void refreshModels(); },
  });
  const entry = connection.data;
  const fault = models.error || save.error || remove.error;
  const back = <Link to="/admin/models"><ArrowLeftOutlined /> 返回供应商连接</Link>;
  if (!entry) return <PageBody>{back}<ErrorAlert error={connection.error} /></PageBody>;
  const active = editing === "new" ? undefined : editing;
  return <PageBody>
    <PageHeader back={back} title={entry.name} description={<span className="mono">{entry.base_url}</span>}
      extra={<Popconfirm title="删除此供应商连接？" description="有历史引用的连接请改为禁用。" onConfirm={() => remove.mutate()}>
        <Button danger loading={remove.isPending}>删除连接</Button>
      </Popconfirm>} />
    <div className="stack">
      <ErrorAlert error={fault} />
      <Card title="连接设置">
        <Descriptions size="small" column={{ xs: 1, sm: 3 }} style={{ marginBottom: 16 }} items={[
          { label: "协议", children: entry.protocol },
          { label: "凭据版本", children: entry.credential_version },
          { label: "创建时间", children: formatTime(entry.created_at) },
        ]} />
        <Form form={form} layout="vertical" initialValues={entry} onFinish={(values) => {
          const patch: Contract["ConnectionPatch"] = { revision: entry.revision, name: values.name, status: values.status };
          if (values.api_key) patch.api_key = values.api_key;
          save.mutate(patch);
        }}>
          <div className="form-grid">
            <Form.Item label="名称" name="name" rules={[{ required: true }]}><Input maxLength={80} /></Form.Item>
            <Form.Item label="状态" name="status"><Select options={[
              { value: "active", label: "启用" }, { value: "disabled", label: "禁用" },
            ]} /></Form.Item>
            <Form.Item label="替换 API Key" name="api_key" extra="留空则保持不变">
              <Input.Password maxLength={8192} autoComplete="off" />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" loading={save.isPending}>保存连接</Button>
        </Form>
      </Card>
      <Card title="模型" styles={{ body: { padding: 0 } }}
        extra={<Space>
          <Button disabled={!untested.length || entry.status !== "active"}
            onClick={() => void startTest(untested.map((m) => m.id))}>
            测试全部未通过{untested.length ? ` (${untested.length})` : ""}
          </Button>
          <Button type="primary" icon={<CloudDownloadOutlined />} onClick={() => setDiscovering(true)}>从供应商拉取模型</Button>
          <Button icon={<PlusOutlined />} onClick={() => setEditing("new")}>手动新增</Button>
        </Space>}>
        <Table rowKey="id" pagination={false} loading={models.isFetching} dataSource={models.data?.items} scroll={{ x: 720 }}
          locale={{ emptyText: <Empty description="此连接下尚无模型" /> }}
          columns={[
            { title: "模型", dataIndex: "display_name" },
            { title: "上游标识", render: (_, model) => <span className="mono">{model.upstream_model}</span> },
            { title: "上下文 / 输出", render: (_, m) => `${fmt(m.context_limit_tokens)} / ${fmt(m.max_output_tokens)}` },
            { title: "状态", render: (_, model) => onOff(model.status) },
            { title: "连接测试", width: 260, render: (_, model) => <TestCell model={model}
              starting={starting.includes(model.id)} start={() => void startTest([model.id])}
              stop={() => void stopTest(model.test_run_id)}
              enabled={model.status === "active" && entry.status === "active"} /> },
            { title: "操作", align: "right", render: (_, model) =>
              <Button size="small" onClick={() => setEditing(model)}>编辑</Button> },
          ]} />
      </Card>
      <PageButtons cursor={cursor} next={models.data?.next_cursor} set={setCursor} />
    </div>
    <DiscoverModal connectionId={entry.id} open={discovering} onClose={closeDiscover}
      onImported={(created) => {
        void refreshModels();
        closeDiscover();
        message.success(created ? `已导入 ${created} 个模型，请逐个测试后启用` : "所选模型均已登记");
      }} />
    <Modal open={Boolean(editing)} title={active ? `编辑 ${active.display_name}` : "新增模型"} footer={null}
      onCancel={() => setEditing(undefined)} destroyOnHidden width={600}>
      {upsert.error && <Alert type="error" showIcon title={upsert.error.message} style={{ marginBottom: 16 }} />}
      <ModelForm key={active?.id ?? "new"} initial={active} submit={active ? "保存模型" : "新增模型"}
        pending={upsert.isPending} onFinish={(values) => upsert.mutate({ model: active, values })} />
    </Modal>
  </PageBody>;
}

export function ModelDirectory() {
  const [cursor, setCursor] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["model-directory", cursor],
    queryFn: () => api<Page<Contract["AvailableModelView"]>>(withCursor("/models", cursor)),
  });
  return <PageBody>
    <PageHeader title="模型目录" description="当前工作区可用于智能体的平台模型；仅显示已启用且连接测试通过的模型" />
    <div className="stack">
      <ErrorAlert error={query.error} />
      <Card styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" pagination={false} loading={query.isFetching} dataSource={query.data?.items}
          locale={{ emptyText: <Empty description="暂无可用模型，请联系管理员配置" /> }} columns={[
            { title: "模型", dataIndex: "display_name" },
            { title: "上下文 Token", render: (_, m) => fmt(m.context_limit_tokens) },
            { title: "输出 Token", render: (_, m) => fmt(m.max_output_tokens) },
            { title: "能力", render: (_, m) => <Space size={4} wrap>
              <Tag color="blue">文本</Tag>
              {m.supports_temperature && <Tag>温度</Tag>}
            </Space> },
          ]} />
      </Card>
      <PageButtons cursor={cursor} next={query.data?.next_cursor} set={setCursor} />
    </div>
  </PageBody>;
}

export function QuotaEditor({ userId }: { userId: string }) {
  const path = `/admin/users/${userId}/quota`;
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["quota", userId], queryFn: () => api<Quota>(path) });
  const save = useMutation({
    mutationFn: (values: Contract["QuotaPatch"]) => api<Quota>(path, "PATCH", values),
    onSuccess: (data) => cache.setQueryData(["quota", userId], data),
  });
  if (query.error) return <ErrorAlert error={query.error} />;
  if (!query.data) return null;
  const policy = query.data;
  return <Form key={policy.revision} layout="vertical" initialValues={policy}
    onFinish={(values: Omit<Contract["QuotaPatch"], "revision">) => save.mutate({ ...values, revision: policy.revision,
      requests_per_minute: values.requests_per_minute ?? null, max_concurrent: values.max_concurrent ?? null })}>
    <Form.Item label="每月 Token 额度" name="monthly_token_limit" rules={[{ required: true }]}>
      <InputNumber min={0} max={10 ** 12} precision={0} style={{ width: "100%" }} />
    </Form.Item>
    <Form.Item label="额度模式" name="mode" extra="软预算超出后仍可调用；严格限制在额度不足时拒绝新调用">
      <Select options={[{ value: "soft", label: "软预算" }, { value: "strict", label: "严格限制" }]} />
    </Form.Item>
    <Form.Item label="每分钟模型调用上限" name="requests_per_minute"
      extra={`留空使用部署默认值（${policy.default_requests_per_minute} 次）。该用户的工作台和接入密钥共享此额度。`}
      rules={[{ type: "integer", min: 1, max: 2147483647, message: "请输入大于零的整数" }]}>
      <InputNumber min={1} max={2147483647} placeholder={`默认 ${policy.default_requests_per_minute}`} style={{ width: "100%" }} />
    </Form.Item>
    <Form.Item label="同时进行的模型调用上限" name="max_concurrent"
      extra={`留空使用部署默认值（${policy.default_max_concurrent} 个）。全平台共享 ${policy.global_max_concurrent} 个并发名额。`}
      rules={[{ type: "integer", min: 1, max: 2147483647, message: "请输入大于零的整数" }]}>
      <InputNumber min={1} max={2147483647} placeholder={`默认 ${policy.default_max_concurrent}`} style={{ width: "100%" }} />
    </Form.Item>
    {save.error && <Alert type="error" showIcon title={save.error.message} style={{ marginBottom: 16 }} />}
    {save.isSuccess && <Alert type="success" showIcon title="额度与限流已保存" style={{ marginBottom: 16 }} />}
    <Button type="primary" htmlType="submit" loading={save.isPending}>保存设置</Button>
  </Form>;
}

const attemptState: Record<string, string> = {
  completed: "success", failed: "error", dispatching: "processing", reserved: "processing",
};

export function Usage() {
  const [cursor, setCursor] = useState<string | null>(null);
  const summary = useQuery({ queryKey: ["usage-summary"], queryFn: () => api<Contract["UsageSummary"]>("/usage/summary") });
  const attempts = useQuery({
    queryKey: ["usage-attempts", cursor],
    queryFn: () => api<Page<Contract["AttemptView"]>>(withCursor("/usage/attempts", cursor)),
  });
  const totals = summary.data;
  const used = totals ? totals.confirmed_tokens + totals.reserved_tokens : 0;
  const percent = totals && totals.token_limit > 0 ? Math.min(100, Math.round((used / totals.token_limit) * 100)) : 0;
  return <PageBody>
    <PageHeader title="用量" description={totals?.period_start
      ? `本月统计 · ${totals.period_start.slice(0, 10)} 至 ${totals.period_end.slice(0, 10)}` : "本月统计"} />
    <div className="stack">
      <ErrorAlert error={summary.error || attempts.error} />
      {totals && <>
        <Card>
          <Space orientation="vertical" style={{ width: "100%" }}>
            <Space wrap>
              <Typography.Text strong>本月 Token 额度</Typography.Text>
              <Tag color={totals.mode === "soft" ? "blue" : "orange"}>{totals.mode === "soft" ? "软预算" : "严格限制"}</Tag>
            </Space>
            <Progress percent={percent} status={percent >= 100 ? "exception" : "normal"}
              format={() => `${fmt(used)} / ${fmt(totals.token_limit)}`} />
          </Space>
        </Card>
        <div className="stat-grid">
          <Card><Statistic title="已确认 Token" value={totals.confirmed_tokens} /></Card>
          <Card><Statistic title="预留 Token" value={totals.reserved_tokens} /></Card>
          <Card><Statistic title="剩余 Token" value={totals.remaining_tokens} /></Card>
          <Card>
            <Statistic title="未知用量" value={totals.unknown_attempts} suffix="次" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              待核对预留 {fmt(totals.uncertain_reserved_tokens)} Token
            </Typography.Text>
          </Card>
          <Card>
            <Statistic title="费用" value="未知" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              费用未知 · {totals.unpriced_attempts} 次未定价
            </Typography.Text>
          </Card>
        </div>
      </>}
      <Card title="调用明细" styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" pagination={false} loading={attempts.isFetching} dataSource={attempts.data?.items} scroll={{ x: 800 }}
          locale={{ emptyText: <Empty description="本月暂无调用" /> }} columns={[
            { title: "时间", render: (_, row) => formatTime(row.started_at) },
            { title: "调用用途", render: (_, row) => <Tag variant="filled">{row.purpose}</Tag> },
            { title: "输入 Token", align: "right", render: (_, row) => row.input_tokens ?? "未知" },
            { title: "输出 Token", align: "right", render: (_, row) => row.output_tokens ?? "未知" },
            { title: "费用", align: "right", render: (_, row) => row.cost ?? "未知" },
            { title: "状态", render: (_, row) => <Badge status={(attemptState[row.state] ?? "default") as "default"} text={row.state} /> },
            { title: "Run", render: (_, row) => <Typography.Text type="secondary" className="mono"
              copyable={{ text: row.run_id }}>{row.run_id?.slice(0, 8)}</Typography.Text> },
          ]} />
      </Card>
      <PageButtons cursor={cursor} next={attempts.data?.next_cursor} set={setCursor} />
    </div>
  </PageBody>;
}
