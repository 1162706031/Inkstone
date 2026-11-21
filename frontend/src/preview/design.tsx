import { ApartmentOutlined, ArrowLeftOutlined, ArrowRightOutlined, CheckOutlined, CodeOutlined, LoginOutlined, RobotOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Empty, Input, Select, Spin, Tabs, Tag } from "antd";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Contract } from "../api/client";
import { ErrorAlert, PageBody, PageHeader } from "../ui";
import { kinds, type Kind, useCatalog } from "./catalog";
import { PreviewNote } from "./components";

const nodes = [
  { id: "input", label: "用户输入", icon: <LoginOutlined />, port: "query", field: "输入说明", value: "用户提交的会议记录" },
  { id: "model", label: "文本整理", icon: <RobotOutlined />, port: "text", field: "系统提示词", value: "提取会议结论，按负责人列出待办事项。" },
  { id: "output", label: "输出结果", icon: <CodeOutlined />, port: "answer", field: "输出字段", value: "summary" },
];
export function WorkflowPreview() {
  const [selected, setSelected] = useState(1);
  const [values, setValues] = useState(nodes.map((n) => n.value));
  const [mode, setMode] = useState("Workflow");
  const node = nodes[selected];
  return <PageBody wide><PageHeader title="工作流设计" back={<Link to="/components/knowledge"><ArrowLeftOutlined /> 能力组件</Link>}
    description="会议纪要 · 工作流草稿" /><PreviewNote />
    <div className="flow-layout">
      <section className="flow-canvas" aria-label="工作流画布"><div className="flow-tools"><strong><ApartmentOutlined /> 编排画布</strong>
        <Select aria-label="工作流模式" value={mode} onChange={setMode} options={[{ value: "Workflow" }, { value: "Chatflow" }]} /></div>
        <div className="flow-path">{nodes.map((n,i) => <div className="flow-step" key={n.id}>
          <button type="button" className={`flow-node node-${n.id}`} aria-pressed={i === selected} onClick={() => setSelected(i)}>
            <span className="flow-node-title">{n.icon} {mode === "Chatflow" && i === 2 ? "回复消息" : n.label}</span>
            <code>{n.port}</code></button>{i < nodes.length - 1 && <ArrowRightOutlined className="flow-arrow" />}
        </div>)}</div>
      </section>
      <Card title="节点配置"><div className="stack"><strong>{mode === "Chatflow" && selected === 2 ? "回复消息" : node.label}</strong>
        <label htmlFor="node-setting">{node.field}</label><Input.TextArea id="node-setting" value={values[selected]} rows={4}
          onChange={(e) => setValues(values.map((v,i) => i === selected ? e.target.value : v))} />
        <span>输出变量 <code>{node.port}</code></span><Button disabled>运行调试（待接入）</Button>
      </div></Card>
    </div></PageBody>;
}

function Bindings({ agent }: { agent: string }) {
  const { resources, bindings, bind } = useCatalog();
  const selected = bindings[agent] ?? [];
  const [kind, setKind] = useState<Kind>("knowledge");
  const [filter, setFilter] = useState("");
  const shown = resources.filter((r) => r.kind === kind && r.name.toLowerCase().includes(filter.trim().toLowerCase()));
  return <Card title="资源绑定" extra={<Tag>{selected.length} 项已选</Tag>}>
    <Tabs activeKey={kind} onChange={(v) => { setKind(v as Kind); setFilter(""); }}
      items={Object.entries(kinds).map(([key,m]) => ({ key, label: m.title }))} />
    <div className="stack"><Input aria-label="筛选绑定资源" placeholder="搜索资源名称" value={filter} onChange={(e) => setFilter(e.target.value)} />
      {shown.length ? shown.map((r) => <div className="binding-row" key={r.id}>
        <span className={`resource-icon ${kind}`}>{kinds[kind].icon}</span><div><strong>{r.name}</strong><p>{r.description}</p></div>
        <Button aria-pressed={selected.includes(r.id)} icon={selected.includes(r.id) && <CheckOutlined />}
          onClick={() => bind(agent, selected.includes(r.id) ? selected.filter((x) => x !== r.id) : [...selected,r.id])}>
          {selected.includes(r.id) ? "解除绑定" : "绑定预览"}
        </Button>
      </div>) : <Empty description="没有匹配的资源" />}
      <Link to={`/components/${kind}`}>管理{kinds[kind].title} <ArrowRightOutlined /></Link>
    </div>
  </Card>;
}

export function AgentDesign() {
  const { id } = useParams();
  const agent = useQuery({ queryKey: ["agent", id], queryFn: () => api<Contract["AgentView"]>(`/agents/${id}`) });
  if (agent.error) return <PageBody><ErrorAlert error={agent.error} /></PageBody>;
  if (!agent.data) return <PageBody><Spin /></PageBody>;
  const base = `/agents/${id}`;
  return <PageBody wide><PageHeader title={agent.data.name} back={<Link to={base}><ArrowLeftOutlined /> 返回智能体配置</Link>} />
    <PreviewNote /><div className="preview-layout"><nav aria-label="智能体扩展配置" className="preview-nav">
      <strong>智能体配置</strong><Link to={base}>基本信息</Link>
      <Link className="active" to={`${base}/preview/bindings`}>资源绑定</Link>
    </nav><div className="preview-main"><Bindings agent={agent.data.id} /></div></div>
  </PageBody>;
}
