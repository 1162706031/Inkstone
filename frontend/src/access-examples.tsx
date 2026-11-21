import { CopyOutlined } from "@ant-design/icons";
import { useQueries, useQuery } from "@tanstack/react-query";
import { App, Button, Empty, Form, Segmented, Select, Tag, Typography } from "antd";
import { useState } from "react";
import type { Contract } from "./api/client";
import { all, ErrorAlert } from "./ui";

const steps = ["创建会话", "发起运行", "订阅事件"] as const;
type Step = typeof steps[number];
const languages = ["curl", "JavaScript", "Python"] as const;
type Language = typeof languages[number];

function publishedModel(agent: Contract["AgentView"]) {
  return agent.published_model_definition_id ??
    (agent.revision === agent.published_revision ? agent.model_definition_id : null);
}

function invocation(agent: Contract["AgentView"], step: Step) {
  const base = `/openapi/v1/agents/${agent.id}`;
  if (step === "创建会话") return {
    method: "POST", path: `${base}/conversations`,
    body: { agent_revision: agent.published_revision, external_user_id: "visitor_example" },
    hint: "将 external_user_id 换成你系统中的用户标识，并保存响应中的 id 作为会话 ID。",
  };
  if (step === "发起运行") return {
    method: "POST", path: `${base}/runs`,
    body: { external_user_id: "visitor_example", conversation_id: "<CONVERSATION_ID>",
      input: { content: [{ type: "text", text: "你好" }] } },
    hint: "填入上一步的会话 ID 和同一用户标识。每条新消息使用唯一的 REQUEST_ID，重试时沿用原值。响应中的 id 即运行 ID。",
  };
  return { method: "GET", path: "/openapi/v1/runs/<RUN_ID>/events",
    hint: "将 RUN_ID 换成上一步返回的运行 ID，保持连接以接收 SSE 事件。" };
}

function example(op: ReturnType<typeof invocation>, variable: string, language: Language) {
  const url = window.location.origin + op.path;
  const headers: Record<string, string> = op.body ? { "Content-Type": "application/json" } : { Accept: "text/event-stream" };
  if (op.body && "conversation_id" in op.body) headers["Idempotency-Key"] = "<REQUEST_ID>";
  const payload = JSON.stringify(op.body, null, 2);
  if (language === "JavaScript") {
    return [
      `const response = await fetch("${url}", {`,
      `  method: "${op.method}",`,
      "  headers: {",
      `    Authorization: "Bearer " + process.env.${variable},`,
      ...Object.entries(headers).map(([k, v]) => `    "${k}": "${v}",`),
      "  },",
      ...(payload ? [`  body: JSON.stringify(${payload.replaceAll("\n", "\n  ")}),`] : []),
      "});", "if (!response.ok) throw new Error(String(response.status));",
      op.body ? "console.log(await response.json());" :
        "for await (const text of response.body.pipeThrough(new TextDecoderStream())) console.log(text);",
    ].join("\n");
  }
  if (language === "Python") {
    return ["import os", "import requests", "", `response = requests.${op.method.toLowerCase()}(`,
      `    "${url}",`, "    headers={", `        "Authorization": "Bearer " + os.environ["${variable}"],`,
      ...Object.entries(headers).map(([k, v]) => `        "${k}": "${v}",`), "    },",
      payload ? `    json=${payload.replaceAll("\n", "\n    ")},` : "    stream=True,",
      "    timeout=60,", ")", "response.raise_for_status()",
      op.body ? "print(response.json())" : "for line in response.iter_lines():\n    print(line.decode())",
    ].join("\n");
  }
  return [`curl ${op.body ? "" : "-N "}-X ${op.method} "${url}"`,
    `  -H "Authorization: Bearer $${variable}"`,
    ...Object.entries(headers).map(([k, v]) => `  -H "${k}: ${v}"`),
    ...(payload ? [`  -d '${payload}'`] : []),
  ].join(" \\\n");
}

export function AccessExamples({ keys, agents, loading }: {
  keys: Contract["KeyView"][]; agents: Contract["AgentView"][]; loading: boolean;
}) {
  const { message } = App.useApp();
  const [keyId, setKeyId] = useState<string>();
  const [agentId, setAgentId] = useState<string>();
  const [step, setStep] = useState<Step>("创建会话");
  const [language, setLanguage] = useState<Language>("curl");
  const models = useQuery({ queryKey: ["available-models"], queryFn: () => all<Contract["AvailableModelView"]>("/models") });
  const availableKeys = keys.filter((key) => key.status === "active" &&
    (!key.expires_at || Date.parse(key.expires_at) > Date.now()));
  const key = availableKeys.find((item) => item.id === keyId);
  const modelNames = new Map(models.data?.map((model) => [model.id, model.display_name]));
  const bound = agents.filter((agent) => key?.agent_ids.includes(agent.id) && agent.published_revision != null);
  // Older catalogs omit the released model ID. A newer draft may reference a different model.
  const missing = bound.filter((agent) => !publishedModel(agent));
  const releases = useQueries({ queries: missing.map((agent) => ({
    queryKey: ["released-model", agent.id, agent.published_version, agent.published_revision],
    queryFn: async () => {
      const versions = await all<Contract["Version"]>(`/agents/${agent.id}/versions`);
      const release = versions.find((v) => v.published_version === agent.published_version && v.revision === agent.published_revision);
      if (!release) throw new Error(`无法读取「${agent.published_name ?? agent.name}」的已发布版本，请刷新页面重试。`);
      return release.config.model_definition_id;
    },
    staleTime: Infinity,
  })) });
  const releaseModels = new Map(missing.map((agent, index) => [agent.id, releases[index].data]));
  const resolving = models.isPending || releases.some((query) => query.isPending);
  const error = models.error ?? releases.find((query) => query.error)?.error;
  const targets = bound.flatMap((agent) => {
    const id = publishedModel(agent) ?? releaseModels.get(agent.id);
    const model = id && modelNames.get(id);
    if (!model) return [];
    return [{ agent, value: agent.id, label: `${model} · ${agent.published_name ?? agent.name}` }];
  });
  const agent = targets.find((target) => target.value === agentId)?.agent;
  const op = agent && invocation(agent, step);
  const variable = key && `INKSTONE_KEY_${key.id.replaceAll("-", "_").toUpperCase()}`;
  const sample = op && variable && example(op, variable, language);
  return <div className="stack">
    <div className="access-selectors">
      <Form.Item label="访问凭证" htmlFor="example-credential">
        <Select id="example-credential" placeholder="选择访问凭证" loading={loading} value={key?.id}
          options={availableKeys.map((k) => ({ value: k.id, label: `${k.name}（${k.key_prefix}…）` }))}
          onChange={(id) => { setKeyId(id); setAgentId(undefined); setStep("创建会话"); }} />
      </Form.Item>
      <Form.Item label="模型与智能体" htmlFor="example-model">
        <Select id="example-model" placeholder="选择模型与智能体" disabled={!key} loading={resolving}
          value={agent?.id} options={targets}
          onChange={(id) => { setAgentId(id); setStep("创建会话"); }} />
      </Form.Item>
    </div>
    <ErrorAlert error={error} />
    {sample && op && key ? <>
      <Typography.Text>在服务端将凭证「{key.name}」的完整密钥保存到环境变量 <code>{variable}</code>。</Typography.Text>
      <Segmented aria-label="调用步骤" value={step} onChange={setStep} options={[...steps]} />
      <div className="endpoint"><Tag color={op.method === "GET" ? "green" : "blue"}>{op.method}</Tag><code>{op.path}</code></div>
      <Typography.Text type="secondary">{op.hint}</Typography.Text>
      <div className="example-box"><div className="example-tools">
        <Segmented aria-label="示例语言" options={[...languages]} value={language} onChange={setLanguage} />
        <Button icon={<CopyOutlined />} size="small" onClick={async () => {
          try { await navigator.clipboard.writeText(sample); message.success("已复制示例"); }
          catch { message.info("请选中代码复制"); }
        }}>复制</Button>
      </div><pre>{sample}</pre></div>
    </> : !loading && !resolving && !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={!availableKeys.length ? "请先创建有效的接入密钥"
        : key && !bound.length ? "此凭证没有可访问的已发布智能体，请检查密钥白名单"
        : key && !targets.length ? "白名单内智能体的已发布模型当前不可用，请检查模型连接和测试状态"
        : "选择访问凭证和模型后即可查看调用示例"} />}
  </div>;
}
