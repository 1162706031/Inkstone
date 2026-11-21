import { ArrowRightOutlined, EditOutlined, FileTextOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Card, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Steps, Table, Tag } from "antd";
import { useState } from "react";
import { Link, Navigate, NavLink, useParams } from "react-router-dom";
import { PageBody, PageHeader } from "../ui";
import { kinds, type Kind, type Resource, useCatalog } from "./catalog";

const categories = Object.entries(kinds) as [Kind, (typeof kinds)[Kind]][];
const options = (values: string[]) => values.map((value) => ({ value, label: value }));

export function PreviewNote() {
  return <p className="preview-note"><span className="preview-led" />前端预览 · 示例数据与修改仅在当前页面会话中展示，刷新后恢复。</p>;
}

function ResourceForm({ kind, initial, onClose, owner }: { kind: Kind; initial?: Resource; onClose: () => void; owner: string }) {
  const { save } = useCatalog();
  const [form] = Form.useForm();
  const [step, setStep] = useState(0);
  const [documents, setDocuments] = useState(initial?.documents ?? []);
  const meta = kinds[kind];
  const configTitle = { sources: "连接配置", knowledge: "文档与检索", skills: "SKILL.md", mcp: "服务端点" }[kind];
  async function next() {
    if (step === 0) {
      try { await form.validateFields(["name", "type", "scope"]); } catch { return; }
      setStep(1);
    } else {
      try { await form.validateFields(); } catch { return; }
      const values = form.getFieldsValue(true);
      save({ ...values, name: values.name.trim(), kind, id: initial?.id ?? crypto.randomUUID(), owner: initial?.owner ?? owner, documents });
      onClose();
    }
  }
  return <Modal open title={(initial ? "编辑" : "创建") + meta.title} onCancel={onClose} width={600}
    footer={<Space>{step === 1 && <Button onClick={() => setStep(0)}>上一步</Button>}
      <Button onClick={onClose}>取消</Button><Button type="primary" onClick={() => void next()}>{step ? "保存预览" : "下一步"}</Button></Space>}>
    <Steps current={step} size="small" className="preview-steps" items={[{ title: "基本信息" }, { title: configTitle }]} />
    <Form form={form} layout="vertical" initialValues={initial ?? { type: meta.types[0], scope: "私人", description: "",
      detail: kind === "knowledge" ? "通用分段" : "", chunk: 512 }}>
      <div hidden={step !== 0}>
        <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}><Input maxLength={80} /></Form.Item>
        <div className="form-grid">
          <Form.Item name="type" label={meta.sample}><Select options={options(meta.types)} /></Form.Item>
          <Form.Item name="scope" label="可见范围"><Select options={options(["私人", "团队"])} /></Form.Item>
        </div>
        <Form.Item name="description" label="描述"><Input.TextArea maxLength={500} rows={2} /></Form.Item>
      </div>
      {step === 1 && (kind === "knowledge" ? <>
        <Form.Item label="文档" extra="只预览文件名。">
          <input aria-label="选择文档" type="file" multiple onChange={(e) => setDocuments([...new Set(Array.from(e.target.files ?? [], (file) => file.name))])} />
          <div>{documents.map((doc) => <Tag key={doc}>{doc}</Tag>)}</div>
        </Form.Item>
        <Form.Item label="分段模式" name="detail"><Select options={options(["通用分段", "父子分段"])} /></Form.Item>
        <Form.Item label="分段长度（字符）" name="chunk" rules={[{ required: true }]}><InputNumber min={64} max={8192} /></Form.Item>
      </> : <Form.Item name="detail" label={kind === "skills" ? "SKILL.md 正文" : "连接地址"}
        rules={[{ required: true, whitespace: true, message: "请填写配置" }]}>
        {kind === "skills" ? <Input.TextArea rows={8} placeholder="# 适用场景、执行步骤与输出格式" /> :
          <Input placeholder={kind === "sources" ? "postgresql://host:5432/database" : "https://host/mcp"} />}
      </Form.Item>)}
    </Form>
  </Modal>;
}

function ResourceList({ kind, owner }: { kind: Kind; owner: string }) {
  const { resources } = useCatalog();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<string>();
  const [editing, setEditing] = useState<Resource | "new">();
  const [selected, setSelected] = useState<Resource>();
  const meta = kinds[kind];
  const rows = resources.filter((r) => r.kind === kind && (!filter || r.type === filter) &&
    (`${r.name} ${r.description} ${r.owner}`).toLowerCase().includes(search.trim().toLowerCase()));
  const isTable = kind === "sources" || kind === "mcp";
  return <div className="preview-main">
    <PageHeader title={`${meta.title}管理`} description={{
      knowledge: "管理可供智能体检索的文档、分段规则和检索方式。", sources: "配置业务数据库的连接信息和所属团队。",
      mcp: "管理智能体可绑定的 MCP 连接与工具。", skills: "维护可复用的业务步骤和输出规范。",
    }[kind]} extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing("new")}>创建{meta.title}</Button>} />
    <PreviewNote />
    <div className="directory-toolbar preview-filter">
      <Input allowClear aria-label="搜索组件" prefix={<SearchOutlined />} placeholder="搜索名称、描述或创建者" value={search} onChange={(e) => setSearch(e.target.value)} />
      <Select allowClear aria-label={meta.sample} placeholder={meta.sample} value={filter} onChange={setFilter} options={options(meta.types)} />
      <Button onClick={() => { setSearch(""); setFilter(undefined); }}>重置</Button>
    </div>
    {isTable ? <Table rowKey="id" dataSource={rows} size="middle" scroll={{ x: 710 }} pagination={{ pageSize: 8, hideOnSinglePage: true }}
      columns={[
        { title: "名称", dataIndex: "name", render: (name, row) => <Button type="link" onClick={() => setSelected(row)}>{name}</Button> },
        { title: meta.sample, dataIndex: "type", render: (type) => <Tag color="blue">{type}</Tag> },
        { title: "连接地址", dataIndex: "detail", ellipsis: true },
        { title: "创建者", dataIndex: "owner" },
        { title: "连接状态", render: () => <Tag>未测试</Tag> },
        { title: "操作", render: (_, row) => <Button type="text" onClick={() => setEditing(row)}>编辑</Button> },
      ]} /> : rows.length ? <div className="resource-grid">{rows.map((r) => <Card key={r.id} className="resource-card">
        <div className="resource-head"><span className={`resource-icon ${kind}`}>{meta.icon}</span><Tag>{r.scope}</Tag></div>
        <h3><button type="button" onClick={() => setSelected(r)}>{r.name}</button></h3><p>{r.description || "暂无描述"}</p>
        <div className="resource-tags"><Tag color="blue">{r.type}</Tag>{kind === "knowledge" && <span><FileTextOutlined /> {r.documents?.length ?? 0} 份文档</span>}</div>
        <div className="resource-footer"><span>{r.owner}</span><Button type="text" size="small" icon={<EditOutlined />} onClick={() => setEditing(r)}>编辑</Button></div>
      </Card>)}</div> : <Empty description="没有匹配的组件" />}
    {editing && <ResourceForm kind={kind} initial={editing === "new" ? undefined : editing} owner={owner} onClose={() => setEditing(undefined)} />}
    <Drawer title={selected?.name} open={!!selected} onClose={() => setSelected(undefined)} size="default">
      {selected && <div className="stack"><Space><Tag color="blue">{selected.type}</Tag><Tag>{selected.scope}</Tag></Space>
        <p>{selected.description}</p>
        {kind === "knowledge" ? <><Card title="文档">{selected.documents?.length ? selected.documents.map((d) => <p key={d}><FileTextOutlined /> {d}</p>) : "暂无文档"}</Card>
          <Card title="分段设置">{selected.detail} · {selected.chunk} 字符</Card></> :
          <Card title={kind === "skills" ? "SKILL.md" : "连接配置"}><pre className="detail-code">{selected.detail}</pre></Card>}
        {kind === "mcp" && <Card title="工具发现"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="接入服务后显示工具和参数" /></Card>}
        <Button onClick={() => { setEditing(selected); setSelected(undefined); }}>编辑配置</Button>
      </div>}
    </Drawer>
  </div>;
}

export function Components({ owner }: { owner: string }) {
  const { category = "knowledge" } = useParams();
  if (!Object.hasOwn(kinds, category)) return <Navigate replace to="/components/knowledge" />;
  return <PageBody wide><div className="preview-layout">
    <nav className="preview-nav" aria-label="组件分类"><strong>能力组件</strong>
      {categories.map(([key, meta]) => <NavLink key={key} to={`/components/${key}`} end>{meta.icon}{meta.title}管理</NavLink>)}
      <Link to="/components/workflows"><ArrowRightOutlined />工作流设计</Link>
    </nav><ResourceList key={category} kind={category as Kind} owner={owner} />
  </div></PageBody>;
}
