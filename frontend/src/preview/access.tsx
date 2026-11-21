import { Button, Card, Descriptions, Drawer, Empty, Input, Select, Table, Tag } from "antd";
import { useState } from "react";

const records = [
  { id: "conv_demo_01", integration: "员工门户", user: "visitor_021", title: "如何申请年假？", answer: "请在员工门户的假期申请页填写起止日期并提交。", time: "09:42", status: "已完成" },
  { id: "conv_demo_02", integration: "服务台", user: "visitor_037", title: "查询工单进度", answer: "用户已取消本次运行。", time: "09:18", status: "已取消" },
];

export function Activity() {
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
