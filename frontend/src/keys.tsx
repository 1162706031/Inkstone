import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, App, Button, Card, DatePicker, Empty, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag,
  Typography,
} from "antd";
import { useState } from "react";
import { api, type Contract } from "./api/client";
import { all, ErrorAlert, formatTime, PageBody, PageHeader } from "./ui";

/** Minimal shape of the value antd's DatePicker yields; avoids depending on dayjs directly. */
type PickerValue = { toISOString: () => string } | null;

type Key = Contract["KeyView"];
type Issued = Contract["IssuedKey"];
type Agent = Contract["AgentView"];

/** Agents a key may be bound to: published, active, and reachable by the signed-in member. */
function bindableAgents(agents: Agent[]) {
  return agents.filter((agent) => agent.status === "active" && agent.published_version !== null);
}

function Secret({ issued, onClose }: { issued: Issued; onClose: () => void }) {
  return (
    <Alert
      type="warning"
      showIcon
      closable={{ onClose }}
      title="接入密钥（仅显示一次，请立即复制保存）"
      description={
        <div className="stack stack-tight">
          <Typography.Text copyable className="mono">{issued.token}</Typography.Text>
          <Typography.Text type="secondary">
            密钥只在此刻可见，关闭后无法再次读取。请把它部署到外部项目的服务端配置中，不要写入浏览器代码或提交到仓库。
            丢失后可使用「轮换」生成新密钥。
          </Typography.Text>
        </div>
      }
    />
  );
}

function KeyForm({ agents, initial, submit, action }: {
  agents: Agent[];
  initial?: Partial<{ name: string; agent_ids: string[]; expires_at: PickerValue }>;
  submit: string;
  action: (values: { name: string; agent_ids: string[]; expires_at: PickerValue }) => Promise<void>;
}) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const options = bindableAgents(agents).map((agent) => ({
    value: agent.id,
    label: `${agent.name}（${agent.is_creator ? "我创建的" : `${agent.creator_name} · 公开`}）`,
  }));
  return (
    <Form form={form} layout="vertical" initialValues={initial} onFinish={async (values) => {
      setBusy(true);
      setError(undefined);
      try {
        await action(values);
      } catch (e) {
        setError(e);
      } finally {
        setBusy(false);
      }
    }}>
      <ErrorAlert error={error} />
      <Form.Item name="name" label="名称" rules={[{ required: true, max: 80 }]}
        extra="用于区分用途，例如「官网客服」或「ERP 问数」。">
        <Input placeholder="官网客服" />
      </Form.Item>
      <Form.Item name="agent_ids" label="可调用的智能体" rules={[{ required: true, message: "请至少选择一个智能体" }]}
        extra="密钥只能调用这里选中的智能体。清单在创建时固定，之后你新增的智能体不会自动加入。只能选择已发布的智能体。">
        <Select mode="multiple" options={options} placeholder="选择智能体"
          notFoundContent={<Empty description="没有已发布的智能体" image={null} />} />
      </Form.Item>
      <Form.Item name="expires_at" label="过期时间" extra="留空表示长期有效。建议为对外密钥设置有效期。">
        <DatePicker showTime style={{ width: "100%" }} placeholder="留空表示不过期" />
      </Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>{submit}</Button>
    </Form>
  );
}

export function AccessKeys() {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const [issued, setIssued] = useState<Issued>();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Key>();
  const list = useQuery({ queryKey: ["access-keys"], queryFn: () => all<Key>("/access-keys") });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => all<Agent>("/agents") });
  const byId = new Map((agents.data ?? []).map((agent) => [agent.id, agent]));
  const refresh = () => cache.invalidateQueries({ queryKey: ["access-keys"] });

  const act = useMutation({
    mutationFn: async ({ key, action }: { key: Key; action: "rotate" | "revoke" }) => {
      setIssued(undefined);
      if (action === "rotate") {
        setIssued(await api<Issued>(`/access-keys/${key.id}/rotate`, "POST", { revision: key.revision }));
        void message.success("已生成新密钥，旧密钥立即失效");
      } else {
        await api(`/access-keys/${key.id}`, "PATCH", { revision: key.revision, status: "revoked" });
        void message.success("密钥已撤销");
      }
    },
    onSettled: refresh,
  });

  return (
    <PageBody wide>
      <PageHeader title="接入密钥"
        description="为外部项目生成密钥，调用你有权使用的智能体。对话记录和用量都归到你的工作区。"
        extra={<Space>
          <Button icon={<ReloadOutlined />} loading={list.isFetching} onClick={() => void list.refetch()}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setIssued(undefined); setCreating(true); }}>
            创建密钥
          </Button>
        </Space>} />
      <div className="stack">
        {issued && <Secret issued={issued} onClose={() => setIssued(undefined)} />}
        <ErrorAlert error={list.error ?? agents.error ?? act.error} />
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" loading={list.isFetching} pagination={false} dataSource={list.data}
            scroll={{ x: 880 }}
            locale={{ emptyText: <Empty description="还没有接入密钥" /> }}
            columns={[
              {
                title: "名称", render: (_, key: Key) => <span>
                  {key.name}<br />
                  <Typography.Text type="secondary" className="mono" style={{ fontSize: 12 }}>
                    {key.key_prefix}…
                  </Typography.Text>
                </span>,
              },
              {
                title: "可调用的智能体", render: (_, key: Key) => <Space size={4} wrap>
                  {key.agent_ids.length === 0 ? <Typography.Text type="secondary">—</Typography.Text>
                    : key.agent_ids.map((id) => {
                      const agent = byId.get(id);
                      return <Tag key={id}>{agent ? agent.name : "已删除的智能体"}</Tag>;
                    })}
                </Space>,
              },
              {
                title: "状态", render: (_, key: Key) => {
                  const expired = key.expires_at !== null && Date.parse(key.expires_at) <= Date.now();
                  if (key.status === "revoked") return <Tag color="red">已撤销</Tag>;
                  return expired ? <Tag color="orange">已过期</Tag> : <Tag color="green">生效中</Tag>;
                },
              },
              { title: "过期时间", render: (_, key: Key) => formatTime(key.expires_at) },
              { title: "最近使用", render: (_, key: Key) => formatTime(key.last_used_at) },
              {
                title: "操作", align: "right", render: (_, key: Key) => key.status === "active" && (
                  <Space size={4} wrap>
                    <Button size="small" onClick={() => { setIssued(undefined); setEditing(key); }}>编辑</Button>
                    <Popconfirm title="轮换密钥？" description="旧密钥立即失效，外部项目需要更新配置。"
                      okText="轮换" cancelText="取消"
                      onConfirm={() => act.mutate({ key, action: "rotate" })}>
                      <Button size="small" disabled={act.isPending}>轮换</Button>
                    </Popconfirm>
                    <Popconfirm title={`撤销「${key.name}」？`}
                      description="撤销后立即拒绝所有新调用，无法恢复。已有的对话记录和用量保留。"
                      okText="撤销" cancelText="取消" okButtonProps={{ danger: true }}
                      onConfirm={() => act.mutate({ key, action: "revoke" })}>
                      <Button size="small" danger disabled={act.isPending}>撤销</Button>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]} />
        </Card>
        <Alert type="info" showIcon title="外部项目如何调用"
          description={<div className="stack stack-tight">
            <Typography.Text>
              在服务端以 <code>Authorization: Bearer &lt;密钥&gt;</code> 依次请求下面三个接口。
              <code>external_user_id</code> 由你的系统标识各个终端用户，平台按它区分会话。
            </Typography.Text>
            <Typography.Paragraph className="mono" style={{ marginBottom: 0 }} copyable>
              {"POST /openapi/v1/agents/{agent_id}/conversations\n"}
              {"POST /openapi/v1/agents/{agent_id}/runs\n"}
              {"GET  /openapi/v1/runs/{run_id}/events"}
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              密钥必须保存在外部项目的服务端，不要下发到浏览器。创建 Run 需要带 <code>Idempotency-Key</code> 请求头。
            </Typography.Text>
          </div>} />
      </div>

      <Modal open={creating} title="创建接入密钥" footer={null} onCancel={() => setCreating(false)} destroyOnHidden>
        <KeyForm agents={agents.data ?? []} action={async (values) => {
          const created = await api<Issued>("/access-keys", "POST", {
            name: values.name,
            agent_ids: values.agent_ids,
            expires_at: values.expires_at ? values.expires_at.toISOString() : null,
          });
          setIssued(created);
          setCreating(false);
          await refresh();
        }} submit="创建" />
      </Modal>

      <Modal open={Boolean(editing)} title={editing && `编辑「${editing.name}」`} footer={null}
        onCancel={() => setEditing(undefined)} destroyOnHidden>
        {editing && <KeyForm agents={agents.data ?? []} submit="保存"
          initial={{ name: editing.name, agent_ids: editing.agent_ids }}
          action={async (values) => {
            await api<Key>(`/access-keys/${editing.id}`, "PATCH", {
              revision: editing.revision,
              name: values.name,
              agent_ids: values.agent_ids,
              expires_at: values.expires_at ? values.expires_at.toISOString() : null,
            });
            setEditing(undefined);
            void message.success("已保存，变更对下一次调用生效");
            await refresh();
          }} />}
      </Modal>
    </PageBody>
  );
}
