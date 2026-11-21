import { ApiOutlined, BookOutlined, DatabaseOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { createContext, useContext, useState, type ReactNode } from "react";

export const kinds = {
  knowledge: { title: "知识库", icon: <BookOutlined />, types: ["混合检索", "向量检索", "关键词检索"], sample: "检索方式" },
  sources: { title: "数据源", icon: <DatabaseOutlined />, types: ["PostgreSQL", "MySQL"], sample: "数据库类型" },
  mcp: { title: "MCP 服务", icon: <ApiOutlined />, types: ["Streamable HTTP", "SSE"], sample: "传输方式" },
  skills: { title: "Skill", icon: <ThunderboltOutlined />, types: ["内容写作", "数据分析", "运营支持"], sample: "业务标签" },
};
export type Kind = keyof typeof kinds;
export type Resource = {
  id: string; kind: Kind; name: string; description: string; type: string; owner: string;
  scope: string; detail: string; documents?: string[]; chunk?: number;
};
const examples: Resource[] = [
  { id: "handbook", kind: "knowledge", name: "员工服务手册", description: "入职、考勤与假期的常见问题及办理流程。", type: "混合检索", owner: "人事团队", scope: "团队", detail: "通用分段", chunk: 512, documents: ["考勤与假期.md", "入职指南.md"] },
  { id: "product", kind: "knowledge", name: "产品使用指南", description: "产品配置步骤、使用说明与常见问题。", type: "向量检索", owner: "产品团队", scope: "团队", detail: "父子分段", chunk: 1024, documents: ["操作手册.md"] },
  { id: "notes", kind: "knowledge", name: "服务台值班笔记", description: "归档值班经验和常见故障处理记录。", type: "关键词检索", owner: "服务台", scope: "私人", detail: "通用分段", chunk: 512, documents: [] },
  { id: "orders", kind: "sources", name: "订单分析库", description: "订单与商品的只读查询。", type: "PostgreSQL", owner: "业务分析组", scope: "团队", detail: "postgresql://orders.example.invalid:5432/analytics" },
  { id: "tickets", kind: "sources", name: "服务工单库", description: "工单流转及处理记录。", type: "MySQL", owner: "服务台", scope: "团队", detail: "mysql://support.example.invalid:3306/tickets" },
  { id: "calendar", kind: "mcp", name: "日程查询服务", description: "查询工作日及团队排班。", type: "Streamable HTTP", owner: "运营团队", scope: "团队", detail: "https://calendar.example.invalid/mcp" },
  { id: "summary", kind: "skills", name: "meeting-summary", description: "将讨论记录整理为结论、待办与负责人。", type: "内容写作", owner: "运营团队", scope: "团队", detail: "# 会议纪要\n\n1. 提取会议结论。\n2. 按负责人列出待办和截止日期。" },
  { id: "report", kind: "skills", name: "weekly-report", description: "基于本周记录撰写工作周报。", type: "运营支持", owner: "服务台", scope: "私人", detail: "# 工作周报\n\n按进展、问题与下周计划组织内容。" },
];
type PreviewState = {
  resources: Resource[];
  save: (row: Resource) => void;
  bindings: Record<string, string[]>;
  bind: (agent: string, ids: string[]) => void;
};
const CatalogContext = createContext<PreviewState | null>(null);

export function PreviewProvider({ children }: { children: ReactNode }) {
  const [resources, setResources] = useState(examples);
  const [bindings, setBindings] = useState<Record<string, string[]>>({});
  return <CatalogContext.Provider value={{ resources, bindings,
    save: (row) => setResources((old) => [row, ...old.filter((x) => x.id !== row.id)]),
    bind: (agent, ids) => setBindings((old) => ({ ...old, [agent]: ids })),
  }}>{children}</CatalogContext.Provider>;
}

export function useCatalog() {
  const value = useContext(CatalogContext);
  if (!value) throw new Error("Missing preview provider");
  return value;
}
