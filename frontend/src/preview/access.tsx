import { ApiOutlined, CopyOutlined, KeyOutlined, MessageOutlined } from "@ant-design/icons";
import { App, Button, Card, Descriptions, Drawer, Empty, Input, Popconfirm, Segmented, Select, Space, Table, Tabs, Tag } from "antd";
import { useState } from "react";
import { PageBody, PageHeader } from "../ui";
import { PreviewNote } from "./components";

const operations = [
  { name: "创建会话", method: "POST", path: "/apps/app_example/conversations", scope: "conversations:write",
    body: { external_user_id: "visitor_example" } },
  { name: "发起运行", method: "POST", path: "/apps/app_example/runs", scope: "runs:create",
    body: { external_user_id: "visitor_example", conversation_id: "conv_example", input: { content: [{ type: "text", text: "如何办理入职手续？" }] } } },
  { name: "订阅事件", method: "GET", path: "/runs/run_example/events", scope: "runs:read" },
] as const;

function example(index: number, language: string) {
  const op = operations[index];
  const url = `https://platform.example.invalid/openapi/v1${op.path}`;
  const headers: Record<string,string> = { Authorization: "Bearer <APPLICATION_KEY>" };
  if ("body" in op) headers["Content-Type"] = "application/json";
  if (index === 1) headers["Idempotency-Key"] = "<REQUEST_ID>";
  if (index === 2) headers.Accept = "text/event-stream";
  const payload = "body" in op ? JSON.stringify(op.body, null, 2) : undefined;
  if (language === "JavaScript") {
    const lines = [`const response = await fetch("${url}", {`, `  method: "${op.method}",`,
      `  headers: ${JSON.stringify(headers, null, 2).replaceAll("\n", "\n  ")},`];
    if (payload) lines.push(`  body: JSON.stringify(${payload.replaceAll("\n", "\n  ")}),`);
    return [...lines, '});', 'if (!response.ok) throw new Error(String(response.status));',
      index === 2 ? 'for await (const text of response.body.pipeThrough(new TextDecoderStream())) console.log(text);' : 'console.log(await response.json());'].join("\n");
  }
  if (language === "Python") return ['import requests', '', `response = requests.${op.method.toLowerCase()}(`,
    `    "${url}",`, `    headers=${JSON.stringify(headers)},`,
    "body" in op ? `    json=${JSON.stringify(op.body)},` : '    stream=True,', '    timeout=60,', ')',
    'response.raise_for_status()', index === 2 ? 'for line in response.iter_lines():\n    print(line.decode())' : 'print(response.json())'].join("\n");
  const lines = [`curl ${index === 2 ? '-N ' : ''}-X ${op.method} "${url}"`,
    ...Object.entries(headers).map(([k,v]) => `  -H "${k}: ${v}"`)];
  if (payload) lines.push(`  -d '${payload}'`);
  return lines.join(` ${String.fromCharCode(92)}\n`);
}

export function AccessPanel() {
  const [iteration, setIteration] = useState(0);
  const [active, setActive] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [step, setStep] = useState(0);
  const [language, setLanguage] = useState("curl");
  const { message } = App.useApp();
  const sample = example(step, language);
  function generate() { setIteration((i) => i + 1); setActive(true); setReveal(true); }
  return <div className="stack">
    <Card title={<Space><KeyOutlined />访问凭证</Space>} extra={<Tag color={active ? "cyan" : undefined}>{active ? "示例凭证" : "未生成"}</Tag>}>
      <div className="preview-key"><Input aria-label="示例 Key" readOnly value={active ? reveal ? `DEMO-NOT-A-KEY-${iteration}` : "••••••••••••" : ""} placeholder="尚无示例 Key" />
        <Space wrap>{active ? <>
          <Button onClick={() => setReveal(!reveal)}>{reveal ? "隐藏" : "显示"}</Button><Button onClick={generate}>轮换示例</Button>
          <Popconfirm title="撤销此示例凭证？" onConfirm={() => { setActive(false); setReveal(false); }}><Button danger>撤销</Button></Popconfirm>
        </> : <Button onClick={generate}>生成示例 Key</Button>}</Space>
      </div>
    </Card>
    <Card title="调用示例">
      <div className="stack">
        <Segmented aria-label="调用步骤" value={step} onChange={setStep}
          options={operations.map((o, i) => ({ label: o.name, value: i }))} />
        <div className="endpoint"><Tag color={step === 2 ? "green" : "blue"}>{operations[step].method}</Tag>
          <code>/openapi/v1{operations[step].path}</code></div>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
          { key: "auth", label: "认证", children: "Bearer 应用 Key" },
          { key: "scope", label: "所需权限", children: operations[step].scope },
          { key: "input", label: "调用参数", children: ["外部用户 ID", "会话 ID、用户 ID、消息、幂等键", "运行 ID"][step] },
          { key: "result", label: "响应", children: ["会话信息", "202 · 运行 ID", "SSE 事件流"][step] },
        ]} />
        <div className="example-box"><div className="example-tools">
          <Segmented aria-label="示例语言" options={["curl", "JavaScript", "Python"]} value={language} onChange={setLanguage} />
          <Button icon={<CopyOutlined />} size="small" onClick={async () => {
            try { await navigator.clipboard.writeText(sample); message.success("已复制示例"); }
            catch { message.info("请选中代码复制"); }
          }}>复制</Button>
        </div><pre>{sample}</pre></div>
      </div>
    </Card>
  </div>;
}

const records = [
  { id: "conv_demo_01", integration: "员工门户", user: "visitor_021", title: "如何申请年假？", answer: "请在员工门户的假期申请页填写起止日期并提交。", time: "09:42", status: "已完成" },
  { id: "conv_demo_02", integration: "服务台", user: "visitor_037", title: "查询工单进度", answer: "用户已取消本次运行。", time: "09:18", status: "已取消" },
];

function Activity() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string>();
  const [record, setRecord] = useState<(typeof records)[number]>();
  const shown = records.filter((r) => (!status || status === r.status) && [r.id,r.integration,r.user,r.title].some((value) => value.includes(query.trim())));
  return <Card title="应用会话记录"><div className="stack">
    <div className="preview-filter"><Input aria-label="搜索会话" placeholder="搜索会话、集成方或外部用户" allowClear value={query} onChange={(e) => setQuery(e.target.value)} />
      <Select aria-label="运行状态" value={status} allowClear placeholder="运行状态" onChange={setStatus} options={["已完成","已取消"].map((value) => ({value}))} /></div>
    <Table size="middle" rowKey="id" dataSource={shown} pagination={false} scroll={{x:580}} locale={{emptyText:<Empty description="没有匹配的会话" />}}
      columns={[{title:"会话",dataIndex:"title",render:(title,row) => <Button type="link" onClick={() => setRecord(row)}>{title}</Button>},
        {title:"集成方",dataIndex:"integration"},{title:"外部用户",dataIndex:"user"},{title:"时间",dataIndex:"time"},
        {title:"状态",dataIndex:"status",render:(value) => <Tag color={value==="已完成" ? "green" : undefined}>{value}</Tag>}]} />
    </div><Drawer title="会话详情" open={!!record} onClose={() => setRecord(undefined)}>{record && <div className="stack">
      <Descriptions column={1} size="small" items={[{key:"id",label:"会话 ID",children:record.id},{key:"user",label:"外部用户",children:record.user}]} />
      <Card size="small" title="用户">{record.title}</Card><Card size="small" title={record.status === "已完成" ? "智能体" : "运行状态"}>{record.answer}</Card>
    </div>}</Drawer></Card>;
}

export function Integrations() {
  return <PageBody><PageHeader title="应用接入" description="示例应用 · 员工服务助手" /><PreviewNote />
    <Tabs items={[
      { key: "access", label: <Space><ApiOutlined />访问 API</Space>, children: <AccessPanel /> },
      { key: "records", label: <Space><MessageOutlined />会话记录</Space>, children: <Activity /> },
    ]} />
  </PageBody>;
}
