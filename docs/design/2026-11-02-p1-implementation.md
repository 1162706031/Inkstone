# P1 基础对话版实施方案

> 日期：2026-11-02（Asia/Shanghai）
>
> 版本：v1.0；状态：实施契约已编写，代码及运行验收未完成。
>
> 上位设计：[总体设计](2026-10-31-ai-platform-refactor.md)。接手状态：[进度说明](refactor-handoff.md)。

本文固定 P1 的默认实现选择。实施者可以在不改变需求、授权和阶段范围的前提下解决内部实现细节，并将变更及理由记入进度说明。不得把尚未验证的依赖、命令或验收标为已经可用。

## 1. 交付范围

P1 面向管理员开通的内部成员，交付个人模型连接、基础智能体配置、文本多轮对话、历史、SSE、取消、基础用量/额度、账号管理及单机部署。每个用户一个个人工作区，管理员也使用个人工作区聊天。

不实现图片、MCP/Tool、子智能体、团队工作区、平台公共模型池、应用发布、应用 Key、开放 API、RAG、自动摘要和模型回退。前端不展示其可操作入口，后端拒绝相关配置字段及非文本输入。旧客服继续使用原入口，P1 不切换其业务流量。

P1 可以独立部署试用。只有本文的阶段任务与验收通过，才能标记 P1 完成；不能将“页面可打开”或“模型回答一次”作为整个阶段完成依据。

## 2. 工程目录与依赖

### 2.1 新旧系统并存

新增 `platform/`，不原地覆盖 `BE/` 或 `FE/my-chat-app/`。平台后端作为一个 Python 包，API 与 Worker 是两个进程入口；不会形成两个独立业务后端。以下目录是待创建的目标，不是现存实现：

```text
platform/
  backend/
    pyproject.toml
    uv.lock
    .python-version
    alembic.ini
    migrations/
    src/ai_platform/
      api/main.py
      api/schemas/
      identity/
      workspaces/
      models/
      agents/
      conversations/
      runs/
      runtime/
      metering/
      audit/
      db/
      worker.py
      cli.py
    tests/unit/
    tests/integration/
    tests/contract/
    tests/provider/
  frontend/
    package.json
    package-lock.json
    .node-version
    src/api/generated/
    src/features/
    src/components/
    src/routes/
    tests/
    e2e/
  contracts/openapi.json
  infra/compose.yaml
  infra/compose.test.yaml
  infra/.env.example
  README.md
```

旧代码仅作参考，角色提示词可在后续迁移；新运行路径不得 import AgentScope 或调用旧全局会话字典。P1 不修改旧接口行为。新 Compose 使用 `ai-platform-p1` 项目名、独立数据库/卷和入口，数据库名为 `ai_platform`，测试库为 `ai_platform_test`；禁止连接旧服务数据库运行新迁移。

### 2.2 依赖候选与锁定门槛

工程选择 Python 3.12、Node.js 24、PostgreSQL 17、Redis 7.4。Python 用 uv，前端用 npm。T01 必须记录实际运行时补丁版本与容器 digest；不使用 `latest` 作为交付镜像标签。

2026-11-02 已在临时目录用 `uv pip compile --python-version 3.12 --universal --no-build` 完成后端候选依赖解析，未安装项目或运行框架。选定直接依赖如下，`langchain-core` 与 `langgraph-checkpoint` 同时列出以固定关键执行组合：

```text
fastapi==0.162.0
uvicorn==0.54.0
pydantic==2.14.2
pydantic-settings==2.16.2
sqlalchemy==2.1.6
alembic==1.20.1
psycopg[binary,pool]==3.3.6
redis==7.4.1
langchain==1.4.7
langchain-core==1.7.3
langchain-openai==1.7.3
langgraph==1.2.19
langgraph-checkpoint==4.3.1
langgraph-checkpoint-postgres==3.2.0
httpx==0.28.1
cryptography==50.0.2
argon2-cffi==25.1.0
pyotp==2.10.0
pytest==9.2.0
pytest-asyncio==1.4.1
```

前端候选为 React/react-dom 19.2.5、TypeScript 5.9.3、Vite 8.0.10、@vitejs/plugin-react 6.0.1、Ant Design 6.6.8、react-router-dom 7.18.5、@tanstack/react-query 5.105.1、Vitest 5.0.5、@playwright/test 1.64.1、openapi-typescript 7.13.0。新增包元数据已查询，尚未执行 npm 安装或构建。当前旧前端使用 TypeScript 6.0.2；新平台暂用 5.9.3，因为所选 openapi-typescript 声明 TypeScript 5.x peer 范围，不直接复制旧 lockfile。版本与约束依据：[openapi-typescript 元数据](https://registry.npmjs.org/openapi-typescript/7.13.0)、[TypeScript 5.9.3 元数据](https://registry.npmjs.org/typescript/5.9.3)。

T01 生成并提交新项目的完整 `uv.lock` 与 `package-lock.json`，验证：安装、受控模型异步流式、usage 采集、禁用 SDK 重试、PostgreSQL checkpoint 写入/恢复、前端类型生成与构建。依赖可解析不等于这些行为兼容；失败时在同一任务中调整版本并记录证据，不能跳过后宣称基础已就绪。生产/CI 使用锁文件安装，不能在启动时升级包。

LangChain 与供应商集成是独立包，具体组合需要验证；相关依据见 [LangChain 安装说明](https://docs.langchain.com/oss/python/langchain/install)。检查点承担线程执行状态，不能替代业务历史，见 [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)。锁文件检查与安装采用 [uv 官方锁定规则](https://docs.astral.sh/uv/concepts/projects/sync/)，Node 兼容性在 T01 按 [Vite 运行要求](https://vite.dev/guide/)复核。

## 3. 产品页面与账号初始化

### 3.1 页面清单

| 路由 | 内容与权限 | 完成条件 |
| --- | --- | --- |
| `/login`、`/activate`、`/reset-password` | 登录、一次性激活/重置；管理员多因素步骤 | 失败状态可见，凭据不写入日志或浏览器持久存储 |
| `/chat`、`/chat/:conversationId` | 选择智能体、发送文本、历史侧栏、停止、重连、删除会话 | 刷新可恢复历史及在途状态，部分输出与终态明确 |
| `/models`、`/models/:id` | 地址、模型标识、Key 替换、能力、连接测试、禁用 | 不回显秘密；测试由用户显式触发并计量 |
| `/agents`、`/agents/:id` | 名称、描述、系统提示词、模型、温度、输出/历史限制 | 保存校验 revision，新会话固定快照，旧会话不受草稿修改影响 |
| `/usage` | 当月 Token、预留、未知用量、费用估算和调用明细 | 不把未知值显示成零，清楚展示软预算或严格限制 |
| `/settings/security` | 修改密码、注销其他会话；管理员 TOTP 设置 | 修改密码撤销旧登录会话 |
| `/admin/users` | 成员创建、激活凭据重发、重置、禁用、基础额度设置 | 仅平台管理员可用，所有变更审计 |

采用 Ant Design 布局/表单/表格，服务端数据由 TanStack Query 管理；表单草稿与 SSE reducer 独立。P1 不增加另一套全局状态框架。手机可完成基础聊天和表单操作；不要求 P1 建立完整可视化设计器。会话标题默认取第一条文本前 30 个字符，可手动改名，不调用模型生成标题。

### 3.2 首个管理员与成员生命周期

- 首次部署通过 `uv run ai-platform bootstrap-admin --username <name>` 创建唯一初始管理员及个人工作区。CLI 交互式读取密码、不接受命令行明文密码；已有管理员时拒绝重复 bootstrap，不设置默认账号/密码。
- 密码为 12–128 字符，使用 Argon2id；登录名统一小写，匹配 `[a-z0-9][a-z0-9._-]{2,63}`。用户名不允许后续修改。管理员 UI 只创建普通成员，P1 不提供 UI 提权。
- 新成员状态为 pending，管理员只看到一次激活 token，通过受控渠道交付；激活设置密码后转 active。重发使旧 token 失效。数据库仅保存 token 摘要，token 不置于查询参数、分析日志或 Referer 可泄露位置。
- 普通成员登录成功建立服务端会话。管理员通过密码后只能获得 5 分钟 preauth 身份：未配置 TOTP 时只能进入设置流程，已配置时必须验证 TOTP，验证后才签发完整管理员会话。
- TOTP 采用 30 秒时间步和允许前后各一步的窗口，保存上次使用计数器拒绝重放。TOTP 种子加密存储；设置后展示一次 10 个单次恢复码，只存摘要。恢复码使用后立即消耗并审计。
- 管理员重置成员密码会立即撤销会话及旧重置 token，生成 30 分钟有效 token。管理员失去 TOTP 和恢复码时，由有主机权限的运维执行 `uv run ai-platform reset-admin-mfa --username <name>`，撤销现有会话，要求下次登录重新设置并审计；不提供绕过 MFA 的公开接口。
- 禁用账号阻止新登录及新执行，并要求 Worker 在后续调用边界停止；已发生 usage 仍结算。禁止禁用最后一位 active 管理员。

### 3.3 会话与 CSRF

同域部署，登录会话使用 `__Host-ai_session` Cookie：HttpOnly、Secure、SameSite=Lax、Path=/，不设置 Domain。数据库存随机会话 token 的 SHA-256 摘要，不返回 JWT 给浏览器存储。身份与个人工作区由后端解析。

`GET /api/v1/auth/csrf` 创建或读取浏览器绑定的短期 CSRF 上下文，并返回 csrf_token；所有非 GET/HEAD 工作台请求发送 `X-CSRF-Token`，包含登录、激活和 MFA。后端同时校验 Origin 与服务端会话/匿名上下文绑定，登录后轮换 CSRF 上下文。单独有 Cookie 或同名 Header 不算通过校验。开发代理保持同域；仅本机开发允许非 Secure Cookie，生产缺少 HTTPS 时拒绝使用该配置。

## 4. P1 API 契约

### 4.1 通用规则与数据结构

前缀 `/api/v1`，JSON 使用 snake_case；ID 为 UUID 字符串，时间为 UTC RFC3339。业务 Schema 拒绝未知字段。成功返回资源本体；列表返回 `{items, next_cursor}`，默认 20、最大 100 条，按 `(created_at, id)` 稳定分页。GET 不产生收费模型调用。

错误统一为 `{error: {code, message, request_id, retryable, fields}}`；fields 是可选字段错误列表。401 未登录、403 已认证但操作不允许、404 资源不存在或不可见、409 revision/幂等/活动 Run 冲突、422 输入错误、429 准入拒绝、503 依赖不可用。异步 Run 已接收后的失败通过 Run 状态与终态事件返回。HTTP 响应带 `X-Request-ID`，所有资源操作重新检查归属。

| Schema | 字段和约束 |
| --- | --- |
| UserView | id、username、display_name、role(member/admin)、status(pending/active/disabled)、created_at；不含密码/TOTP/会话摘要 |
| ConnectionCreate | name(1–80)、base_url(HTTPS，最长 2048)、api_key(非空，最长 8192，仅写入)、protocol 固定 chat_completions；P1 不允许自定义认证 Header |
| ConnectionView | id、name、base_url、protocol、status(active/disabled)、credential_version、has_credential、revision、last_test_status、created_at；不含 api_key |
| ModelCreate | upstream_model(1–200)、display_name、context_limit_tokens(正数)、max_output_tokens(正数)、supports_temperature(boolean)、usage_support(unknown/supported/unsupported)；均需实际验证，成员不可自行赋予严格额度资格 |
| ModelView | id、connection_id、上述目录字段、status、revision、strict_budget_eligible、capability_status(unverified/verified)；服务端检测结果为权威 |
| AgentConfig | name(1–80)、description(最多 500)、system_prompt(最多 16000 字符)、model_definition_id、temperature(可空，0–2 且模型支持)、max_output_tokens(默认 1024)、history_max_messages(默认 20，上限 100) |
| AgentView | id、AgentConfig 字段、revision、status(active/archived)、created_at、updated_at；版本另返 id、revision、配置快照及 created_at |
| ConversationView | id、agent_id、agent_version_id、title、status(active/deleting)、active_run_id(可空)、created_at、updated_at |
| MessageView | id、conversation_id、run_id、sequence、role(user/assistant)、content(有序 text 块，含 block_id/text)、status(accepted/streaming/completed/failed/cancelled/interrupted)、created_at |
| RunView | id、conversation_id(连接测试可空)、kind(chat/connection_test)、agent_version_id(测试可空)、status、error(可空)、input_message_id(可空)、output_message_id(可空)、created_at、started_at、finished_at、last_event_sequence |
| UsageSummary | period_start/end、timezone、mode(soft/strict)、token_limit、confirmed_tokens、reserved_tokens、uncertain_reserved_tokens、unknown_attempts、remaining_tokens、estimated_costs(按币种数组)、unpriced_attempts |
| AttemptView | id、model_call_id、run_id、model_definition_id、state、input_tokens/output_tokens(可空)、cost/currency(可空)、usage_source(reported/unknown)、started_at、finished_at、error_code；不返回上游请求/响应正文 |

模型 context_limit_tokens 为连接配置声明，未验证时不能承诺上游严格支持；派发前还校验平台字符/字节及上下文限制。配置修改都携带 revision，更新原子比较后递增；模型地址、上游模型名、影响输入输出的能力修改产生新资源版本，旧智能体快照使用旧配置引用，凭据轮换和禁用属于实时策略。

### 4.2 身份与管理接口

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| GET `/auth/csrf` | 无 | 200 `{csrf_token}` |
| POST `/auth/login` | `{username, password}` | 成员：200 `{user, next:"ready"}` + Cookie；管理员：200 `{next:"mfa_setup"或"mfa_verify"}` + 受限 preauth Cookie |
| POST `/auth/activate` | `{token, password}` | 200 `{status:"activated"}`，随后正常登录 |
| POST `/auth/reset-password` | `{token, new_password}` | 200 `{status:"password_changed"}`，撤销旧会话后正常登录 |
| POST `/auth/reauthenticate` | 完整管理员会话 + `{password, code}`（当前 TOTP） | 200 `{verified_until}`，在服务端记录 5 分钟敏感操作窗口 |
| POST `/auth/mfa/setup` | 尚未配置 MFA 的 enrollment preauth，或通过最近认证的完整管理员会话 | 200 `{secret, provisioning_uri}`，仅显示待确认种子，不直接生效 |
| POST `/auth/mfa/confirm` | `{code}` + 有效设置上下文 | 200 `{user, recovery_codes}` + 完整会话 Cookie |
| POST `/auth/mfa/verify` | `{code}` 或 `{recovery_code}` 二选一 | 200 `{user}` + 完整会话 Cookie |
| GET `/auth/me` | 完整登录会话 | 200 `{user, workspace_id, csrf_token}` |
| POST `/auth/logout` | 当前登录会话 | 204，撤销会话并清 Cookie |
| POST `/auth/change-password` | `{current_password, new_password}`；管理员另要求近期 MFA | 204，撤销全部会话并要求重新登录 |
| POST `/auth/revoke-other-sessions` | 完整会话及当前密码 `{password}` | 204，保留本次会话，撤销其他会话 |
| GET `/admin/users` | cursor、limit、status 可选过滤 | 200 UserView 列表 |
| POST `/admin/users` | `{username, display_name}` | 201 `{user, activation_token, expires_at}`，一次展示 |
| POST `/admin/users/{id}/activation-token` | pending 成员 | 201 `{activation_token, expires_at}`，替换旧 token |
| POST `/admin/users/{id}/password-reset` | active 成员 | 201 `{reset_token, expires_at}` |
| PATCH `/admin/users/{id}` | `{status:"active"或"disabled", revision}` | 200 UserView（管理视图另含 revision）；只有已激活且有密码的账号可恢复 active，不能通过先禁用 pending 账号绕过激活 |
| GET `/admin/users/{id}/quota` | 无 | 200 `{workspace_id, mode, monthly_token_limit, revision}` |
| PATCH `/admin/users/{id}/quota` | `{monthly_token_limit, mode, revision}` | 200 同上；仅更新额度策略，不伪造历史用量；降低到已用量以下时停止新准入 |

P1 管理员不获得私人聊天正文查询接口。MFA 种子与恢复码不得缓存到查询状态持久层；setup/confirm 必须一次性、过期和重放保护，已有 MFA 的 login preauth 不能用于重新设置种子。更换 MFA 需近期密码和当前 TOTP 验证，成功后替换恢复码并撤销其他会话；无当前 TOTP 时使用运维恢复流程。

### 4.3 模型与智能体接口

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| GET/POST `/model-connections` | GET 分页；POST ConnectionCreate | 200 ConnectionView 列表 / 201 ConnectionView |
| GET `/model-connections/{id}` | 无 | 200 ConnectionView |
| PATCH `/model-connections/{id}` | `{revision, name?, base_url?, api_key?, status?}`；至少一个修改字段 | 200 ConnectionView；不接受空 Key 代表“保留” |
| DELETE `/model-connections/{id}` | 无 | 无引用时 204；被历史快照引用返回 409，允许改为 disabled |
| GET/POST `/model-connections/{id}/models` | GET 分页；POST ModelCreate | 200 ModelView 列表 / 201 ModelView |
| PATCH `/models/{id}` | `{revision, display_name?, upstream_model?, context_limit_tokens?, max_output_tokens?, supports_temperature?, status?}` | 200 ModelView；能力变更使验证失效 |
| POST `/model-connections/{id}/test` | `{model_definition_id}` + Idempotency-Key | 202 RunView；固定短测试提示词及最多 32 输出 Token，按个人用量记录 |
| GET/POST `/agents` | GET 分页；POST AgentConfig | 200 AgentView 列表 / 201 AgentView |
| GET/PATCH `/agents/{id}` | GET 无；PATCH `{revision, ...AgentConfig修改字段}` | 200 AgentView |
| POST `/agents/{id}/validate` | 已保存草稿 | 200 `{valid, errors:[{field,code,message}]}`；只做本地配置检查，不偷偷发模型请求 |
| GET `/agents/{id}/versions` | 分页 | 200 不可变版本列表 |
| DELETE `/agents/{id}` | 无 | 204，标记 archived，拒绝新会话；既有会话保留查看，明确停止续聊 |

不自动依赖上游 `/models` 返回模型目录：用户手动填写 model ID，先测试再使用。测试失败不删除凭据，也不把连接自动切到其他供应商。

### 4.4 对话、运行与用量接口

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| POST `/agents/{id}/conversations` | `{agent_revision, title?}` | 201 ConversationView；验证后为该 revision 创建/复用不可变 agent_version |
| GET `/conversations` | cursor、limit、agent_id 可选 | 200 ConversationView 列表 |
| GET/PATCH `/conversations/{id}` | GET 无；PATCH `{title}` | 200 ConversationView；不能改版本或工作区 |
| GET `/conversations/{id}/messages` | cursor、limit | 200 `{items, next_cursor, run_cursors}`，快照和各根 Run 水位在同一数据库视图读取 |
| DELETE `/conversations/{id}` | 无 | 无活动 Run 时 202 `{status:"deleting"}`；活动中返回 409，须先取消并等终态 |
| POST `/agents/{id}/runs` | `{conversation_id, input:{content:[{type:"text",text}]}}` + Idempotency-Key | 202 RunView；P1 只允许一个非空 text 块，后端生成 block_id |
| GET `/runs/{id}` | 无 | 200 RunView，含可见输出消息快照 |
| GET `/runs/{id}/events` | `after` 或 Last-Event-ID | 200 text/event-stream；游标过期返回 410 + `EVENT_REPLAY_EXPIRED` |
| POST `/runs/{id}/cancel` | 无 | 202 RunView；已终态时 200 原 Run，不改变终态 |
| GET `/usage/summary` | month=`YYYY-MM`，默认当月 | 200 UsageSummary，仅当前个人工作区 |
| GET `/usage/attempts` | cursor、limit、from/to、status、model_definition_id 可选 | 200 AttemptView 列表，最长查询区间 90 天 |
| GET `/health/live`、`/health/ready` | 无业务身份，受网络入口约束 | 200 `{status:"ok"}`；ready 检查 DB/Redis，失败 503，不泄露配置 |

聊天幂等作用域为 `(workspace_id, user_id, agent_id, Idempotency-Key)`，保留 24 小时；同 Key 同请求返回原 Run，同 Key 不同请求返回 409 `IDEMPOTENCY_CONFLICT`。连接测试使用单独 operation/connection 作用域。活动会话遇到其他幂等键返回 409 `CONVERSATION_BUSY`。唯一约束必须能处理并发提交，不只靠先查询。

```json
{
  "conversation_id": "5e1fbc39-fbeb-4d74-9f3b-2ecfbd0f7347",
  "input": {"content": [{"type": "text", "text": "你好，请介绍一下你的能力"}]}
}
```

SSE 完整信封、append/snapshot、去重和终态遵循总体设计第 10.3 节。P1 使用 message/run/usage 事件，不预先实现工具和子智能体事件。会话分页中各消息携带其 Run，客户端只对已加载消息和当前活动 Run 订阅；历史内容读取不得触发图执行。

## 5. P1 数据库与迁移

### 5.1 公共约定

业务 schema 为 `app`，LangGraph checkpointer 使用独立 `graph` schema。主键为 UUID，时间用 timestamptz；所有有状态资源含 created_at/updated_at，用户可修改配置含 revision int（从 1 开始）。枚举用 text + CHECK，计数用 bigint，费用用 numeric(20,8) 和 currency；JSON 用 jsonb，不以 JSON 存放可恢复明文凭据。TOTP 密文列采用包含 nonce 与密文的版本化二进制封装，不能重复使用 nonce。

下表列出 P1 必需字段。`?` 表示可空；其余业务字段非空。外键及租户内一致性使用 `(workspace_id,id)` 唯一键与复合外键，业务代码不得接受任意 workspace_id。仅 users、账号会话及管理员审计具有平台级归属。P1 先以服务层显式鉴权加数据库关系约束实现隔离，不把未验证的 RLS 策略当作完成条件。

| 表 | 主要字段（除通用字段外） | 关键约束/索引 |
| --- | --- | --- |
| users | username text、display_name text、password_hash text?、role text、status text、revision int、mfa_secret_ciphertext bytea?、mfa_key_version text?、mfa_last_counter bigint? | unique(username)，role/status CHECK，pending 可无密码，active 必须有密码 |
| workspaces | owner_user_id uuid、kind text=personal、status text | unique(owner_user_id)，P1 CHECK kind=personal；P5 迁移扩展，不提前创建团队成员表 |
| auth_sessions | user_id uuid?、token_hash bytea、csrf_hash bytea、stage(anonymous/preauth/full)、purpose?、expires_at、idle_expires_at、revoked_at?、last_seen_at、pending_mfa_ciphertext?、pending_mfa_key_version? | unique(token_hash)，索引(user_id,revoked_at)、expires_at；匿名上下文无 user |
| verification_tokens | user_id、purpose(activation/password_reset)、token_hash、expires_at、consumed_at? | unique(token_hash)，同用户/用途未消费 token 部分唯一；重发先原子失效旧记录 |
| mfa_recovery_codes | user_id、code_hash、consumed_at? | unique(user_id,code_hash)，消费用条件更新 |
| credential_secrets | workspace_id、ciphertext bytea、nonce bytea、key_version text、version int | 仅凭据服务可解密；工作区与资源标识作为认证附加数据 |
| model_connections | workspace_id、name、base_url、protocol、credential_id、credential_version、status、revision、last_test_status? | 复合外键到凭据，索引(workspace_id,status) |
| model_definitions | workspace_id、connection_id、upstream_model、display_name、context_limit_tokens、max_output_tokens、supports_temperature、usage_support、capability_status、strict_budget_eligible、status、revision | unique(connection_id,upstream_model)，Token 上限 > 0 |
| model_config_versions | workspace_id、connection_id、model_definition_id、connection_revision、model_revision、snapshot jsonb | unique(connection_id,model_definition_id,connection_revision,model_revision)；快照不含 Key，持有稳定 credential 引用，凭据取当前版本 |
| agents | workspace_id、AgentConfig 字段、status、revision | 同工作区模型外键，索引(workspace_id,status,updated_at) |
| agent_versions | workspace_id、agent_id、revision、model_config_version_id、config jsonb、config_hash text | unique(agent_id,revision)，不可变 |
| conversations | workspace_id、owner_user_id、agent_id、agent_version_id、title、status、next_message_seq bigint、thread_id uuid、deletion_requested_at? | unique(thread_id)，索引(workspace_id,created_at,id) |
| messages | workspace_id、conversation_id、run_id、sequence bigint、role、content jsonb、status、projected_event_seq bigint | unique(conversation_id,sequence)，unique(run_id,role)，P1 一次 Run 一条输入/一条回答 |
| runs | workspace_id、actor_user_id、conversation_id?、agent_version_id?、kind、status、input_message_id?、output_message_id?、checkpoint_id?、thread_id?、fencing_token bigint、last_event_seq bigint、error_code?、started_at?、finished_at?、payload_purged_at? | conversation_id 在 queued/running/cancel_requested 状态部分唯一；未清理的 chat 必须有会话与版本，test 必须有模型调用归属；只有终态可标记 payload_purged_at 并清空正文关联 |
| execution_jobs | run_id、status(ready/leased/done)、lease_owner?、lease_until?、fencing_token bigint、available_at | unique(run_id)，索引(status,available_at,lease_until)，根 Run 与任务同事务写入 |
| idempotency_records | workspace_id、actor_user_id、operation、resource_id、key_hash、request_hash、run_id、expires_at | unique(workspace_id,actor_user_id,operation,resource_id,key_hash)，索引 expires_at |
| run_events | root_run_id、run_id、sequence bigint、schema_version int、type、payload jsonb、created_at | unique(root_run_id,sequence)，索引 created_at；P1 root_run_id=run_id |
| model_calls | workspace_id、run_id、model_definition_id、logical_call_key、purpose(chat/connection_test)、status | unique(run_id,logical_call_key) |
| model_call_attempts | model_call_id、workspace_id、attempt_number int、state、credential_version、dispatched_at?、deadline_at、provider_request_id?、result_message_id?、recoverable_result jsonb?、result_expires_at?、error_code? | unique(model_call_id,attempt_number)，索引(state,deadline_at)；可恢复结果属于受保护业务数据，不写通用日志 |
| usage_records | attempt_id、workspace_id、input_tokens?、output_tokens?、raw_usage jsonb?、normalized_usage jsonb?、estimated_cost?、currency?、price_snapshot jsonb?、source | unique(attempt_id)，reported/unknown CHECK，未知不强制填 0 |
| quota_policies | workspace_id、mode、monthly_token_limit bigint、revision | unique(workspace_id)，limit >= 0 |
| quota_buckets | workspace_id、period_start、period_end、limit_tokens、settled_tokens、reserved_tokens | unique(workspace_id,period_start)，计数非负 |
| quota_reservations | attempt_id、bucket_id、reserved_tokens、state、expires_at、settled_at? | unique(attempt_id)，索引(state,expires_at) |
| quota_ledger | workspace_id、bucket_id、attempt_id?、operation、amount bigint、settlement_key、reason、actor_user_id?、created_at | unique(settlement_key)，追加流水，不覆盖历史；更正用新 adjustment |
| audit_events | actor_user_id?、workspace_id?、action、target_type、target_id?、result、request_id、safe_details jsonb、created_at | 索引(workspace_id,created_at)、(target_type,target_id,created_at)；不含密码、token 和正文 |

messages 与 runs 的循环引用在迁移中后建外键，事务内预分配 UUID 并使用可延迟约束，最终提交时校验输入/输出归属；不能只为省事移除关系约束。删除会话不级联删除 usage、账本和审计：其关联终态 Run 标记 payload_purged_at，清空输入/输出消息、会话和 thread/checkpoint 引用，保留最小计量元数据；清理可恢复正文后再删除消息与会话。purged 状态不能再进入执行或用于恢复。P1 直接用户身份来源于 workspace.owner_user_id，P5 增加 memberships 与应用身份。

### 5.2 迁移顺序

| 迁移 | 内容 | 校验 |
| --- | --- | --- |
| 0001_identity | users、个人 workspaces、auth_sessions、verification_tokens、MFA 恢复码、audit_events | 空库升级、唯一性、bootstrap 幂等拒绝 |
| 0002_models | 凭据、连接、模型目录/版本、智能体/版本 | 跨工作区引用拒绝，版本不可变，Key 不进入快照 |
| 0003_execution | 会话/消息、Run、任务、事件、幂等 | 并发同会话只接受一个 Run，终态原子提交 |
| 0004_metering | 调用/尝试、usage、基础额度、预留、账本 | 并发预留与重复结算正确，未知用量保留 |

图检查点使用固定版本 checkpointer 的 setup，通过 `uv run ai-platform init-checkpoints` 在部署迁移阶段一次性执行并记录版本，不由每个 Worker 启动竞争创建。T01 必须验证独立 graph schema 与连接设置；不兼容时在该任务记录替代配置。发布顺序为备份 → 业务迁移 → checkpoint 初始化/升级 → API/Worker；迁移失败不启动新版本。回退优先应用版本回退，破坏性 schema downgrade 只在隔离测试库演练，生产恢复使用已验证备份流程。

## 6. 模型、执行和计量的具体选择

### 6.1 首批协议与网络边界

P1 协议固定为兼容 Chat Completions 的文本接口，base_url 为 API 根路径，适配器只追加固定聊天路径。支持 system/user/assistant 文本及 SSE；不自动切到另一种 API，不发送工具、图片或供应商未知参数。SDK 自动重试设为 0，P1 统一入口默认也不自动重试；用户明确重新发送创建新 Run。平台不要求购买特定供应商服务，实际地址与 Key 由成员配置。

成员可填公网 HTTPS 地址；内网目标只允许运维配置的主机/端口/路径白名单，私网 HTTP 还需单独允许。拒绝 URL 内嵌用户名密码、fragment、任意 query 认证和重定向；DNS 校验结果必须落实到实际连接地址，同时保留正确 TLS 主机校验。代理不能绕过同一策略。测试和正式请求使用同一出站实现。

业务实现不依赖真实付费接口才能运行测试。T01/T03 创建受控假供应商，覆盖正常文本流、结束 usage、无 usage、429、超时、断流和重复返回；实际连接只在显式 provider 验证任务中调用并计量。假供应商只在测试网络内放行，生产配置禁止该例外。

### 6.2 执行与恢复

使用 LangChain `create_agent` 的无工具模式与 PostgreSQL checkpointer；平台自定义状态包含稳定消息 ID 和 applied_input_seq。P1 不另写一套 ReAct 循环。受控模型包装器在每次请求前注入运行身份、预算和取消检查；模型原始结果不直接成为业务接口。

Worker 条件领取 execution_jobs，持有租约与 fencing token，启动时及定期扫描待恢复任务。当前会话正常续聊只增量注入新输入；固定系统提示词来自 agent_version。上下文窗口仅取最近已成功轮次及当前输入，丢弃历史窗口内较旧的完整轮次，不改写原始业务记录；超出上下文时明确失败。失败/取消/不确定的轮次默认不成为下一轮模型上下文。

在 checkpoint 已记录失败轮次或无法安全继续时，为后续新 Run 建立新 thread_id，一次性从成功业务轮次初始化并记录来源，不能向旧 thread 重复追加整段历史。恢复已完成的图只补业务投影；上游结果不确定进入 interrupted，不自动重新生成。临时可恢复模型结果保留到投影完成后 24 小时，待核对记录最长 7 天；即使正文清理，用量待核对状态仍保留。

SSE 增量及消息快照投影同事务提交，每 100 ms 或 4 KiB 批量落盘，终态强制 flush；发送通知在提交后。浏览器断连不取消 Run；取消 queued Run 可直接终止，running Run 进入 cancel_requested 并阻止后续派发。取消仍可能收到上游 usage，独立结算。执行结束释放资源；终态不能重新变回 running。

### 6.3 基础额度与默认参数

以下为可配置产品默认值，不是容量承诺；部署容量在 T07 验证。

| 参数 | P1 默认 | 执行规则 |
| --- | --- | --- |
| 激活 / 重置 / preauth 有效期 | 24 小时 / 30 分钟 / 5 分钟 | 一次性 token 原子消费，重发撤销旧值 |
| 登录会话 | 绝对 7 天、空闲 12 小时 | 两者较早者到期；用户禁用立即撤销 |
| CSRF 匿名上下文 | 30 分钟 | 有界存储/清理，登录后轮换 |
| 登录/MFA 限流 | 单账号每 15 分钟 5 次失败；单 IP 每 15 分钟 30 次 | Redis 共享计数，返回 429；不永久锁死账号 |
| 模型请求 | 每工作区 20 次/分钟、并发 2；Worker 全局并发 4 | 测试调用同样占用，账号禁用停止新准入 |
| 文本输入 | 每消息最多 8000 字符且 UTF-8 最多 32 KiB；HTTP JSON 最大 64 KiB | 双重限制；平台提示词最多 16000 字符，仍计入模型上下文 |
| 输出 / 历史 | 默认 1024、平台最大 4096 Token；历史默认 20、最大 100 条 | 同时取模型上限的较小值；按完整轮次截断 |
| 时间限制 | 连接 10 秒、首片段 30 秒、总模型请求 120 秒、Run 180 秒、排队 300 秒 | 总 deadline 必须真正取消任务，不能只配置单次 socket read 超时 |
| Worker 租约 / 心跳 / 扫描 | 30 秒 / 10 秒 / 15 秒 | 资源并发占位按上游 deadline 保守保留，不能随 Worker 租约一并释放 |
| 月度额度 | 1,000,000 Token / 个人工作区；月界按 Asia/Shanghai，存 UTC | 管理员可调，0 表示禁止新模型调用；不包含充值/追加额度桶 |
| 预算模式 | 默认 soft；strict 仅运维验证的模型支持 | 页面明确限制强度，不把不可靠估算称为硬额度 |
| SSE | 心跳 15 秒、批次 100 ms/4 KiB、事件保留 7 天 | 慢订阅断开后重放；消息正文独立保存 |
| 保留 | 聊天 180 天、应用日志 14 天、审计/usage/账本 365 天、备份 30 天 | 每日清理；历史不足时显示已过期，不通过图恢复已删正文 |

soft 模式也记录用量并在确认消耗加现有占用达到月限时拒绝新准入；未知模型输入先按保守估算预留，费用/Token 实际值以供应商 usage 为准，明确可能发生估算偏差和在途超额。strict 资格由运维在模型验证后配置，要求可验证的输入计数和输出上界；工作区切 strict 后，未具备资格的模型返回 `STRICT_BUDGET_UNSUPPORTED`，不能静默软化限制。

每次尝试派发前保存预留与派发意图，结算唯一键固定为 `attempt_id:settle`，未知使用量进入 uncertain 并继续占用。月度策略变更同时更新当期 bucket 限额，不变更已结算消耗；跨月尝试仍记准入月份。P1 不提供用户自行退款接口：运维通过有审计的 `ai-platform reconcile-attempt` 命令输入确认 usage 或供应商无消耗证据，追加调整流水。仅超时、缺失 usage、用户点取消均不是零消耗证据。

凭据使用 AES-256-GCM，密钥从部署 secret 文件加载，版本化保存，不能存同一业务库。auth token 使用加密安全随机值，摘要与密码散列分开处理。备份包含加密业务库及 graph，解密材料独立保管；恢复验证必须包含凭据可解密。

删除会话立即隐藏且禁止新执行，后台 24 小时内清理消息、checkpoint、事件与可恢复结果；账本和必要审计只保留最小元数据。备份中的旧正文随 30 天保留期过期，恢复后必须按删除记录和保留规则再次清理，不能让已删除会话重新可见。

## 7. 启动、契约与测试命令

**以下均为 T01–T07 要实现的命令契约，现在尚无对应平台目录或可运行命令。** 仓库根目录执行 Compose；Python/npm 命令分别在 platform/backend、platform/frontend 执行。T01 在 platform/README.md 写明首次锁定、环境准备和启动步骤。

```bash
# 仓库根目录：项目名隔离，首次按 .env.example 配置真实 secret 文件路径
docker compose --env-file platform/infra/.env -p ai-platform-p1 -f platform/infra/compose.yaml config --quiet
docker compose --env-file platform/infra/.env -p ai-platform-p1 -f platform/infra/compose.yaml up -d postgres redis
```

```bash
# platform/backend；已有锁文件后
uv sync --locked --group dev
uv run alembic upgrade head
uv run ai-platform init-checkpoints
uv run ai-platform bootstrap-admin --username admin
uv run uvicorn ai_platform.api.main:app --host 127.0.0.1 --port 8081
# 另一终端，相同环境配置
uv run python -m ai_platform.worker
uv run ai-platform export-openapi --output ../contracts/openapi.json
uv run pytest tests/unit tests/contract
uv run pytest tests/integration
```

```bash
# platform/frontend；已有锁文件后
npm ci
npm run api:generate
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npm run test:e2e
```

`api:generate` 使用 openapi-typescript 从 `../contracts/openapi.json` 生成 `src/api/generated/schema.d.ts`；请求函数使用统一 typed fetch 封装。接口变更后重新导出/生成，CI 比较产物，不能靠手写前端字段消除类型错误。npm scripts 的上述名称必须实现；E2E 使用 Playwright，禁止空脚本或全 skip 充数。

测试 Compose 项目名 `ai-platform-p1-test`，只暴露环回测试端口 PostgreSQL 55433、Redis 56380；开发端口分别 55432、56379，后端 8081、前端 5174。本地 backend Settings 统一读取 `platform/backend/.env`，CLI、API 与 Worker 共用该设置；容器通过 env_file/secret 传入，真实 .env 不入 Git。关键变量为 DATABASE_URL、CHECKPOINT_DATABASE_URL、REDIS_URL、APP_ORIGIN、CREDENTIAL_KEY_FILE、CREDENTIAL_KEY_VERSION、PRIVATE_MODEL_ALLOWLIST、ENVIRONMENT，示例文件只写占位说明。pytest 使用 `TEST_DATABASE_URL`/`TEST_REDIS_URL` 和同一测试库的独立 graph schema，验证库名和测试环境后才能清库，禁止回落到生产 DATABASE_URL。

前端开发服务器代理 `/api` 到 8081，生产由同一 HTTPS 反向代理提供前端和 `/api`。API 容器监听内部接口但不直接暴露管理入口。生产完整启动命令沿用上面的 Compose 配置执行 `up -d --build`，迁移/初始化作为显式一次性任务，备份/恢复命令由 T07 写入部署 README 并实际演练。后端日志、测试快照与命令输出不得包含配置秘密。

`tests/provider` 默认不运行，需显式提供测试连接并设置 `RUN_PROVIDER_TESTS=1`；完成一条真实文本流、usage 与取消验证后记录供应商、模型、协议、耗时及结果，不记录 Key。没有有效凭据时，该项记录“待验证”，不能把假供应商成功当成真实接口兼容证据。

## 8. 任务与完成标准

| 任务 | 依赖 / 交付 | 验收证据 |
| --- | --- | --- |
| T01 工程与兼容性 | 独立目录、运行时、锁文件、最小 API/Worker、假供应商、checkpoint 探针、前端构建及 Compose | 记录实际版本；安装/构建通过；两个图调用在重启后不重复输入，受控模型保留流式和 usage；不建设业务页面 |
| T02 身份与持久化 | T01；0001、bootstrap、激活/重置、MFA、CSRF、账号/个人工作区页面 | 成员不能公开注册；MFA 未完成无法访问管理接口；禁用/改密撤销会话；跨用户资源拒绝 |
| T03 模型与计量 | T02；按序执行 0002–0004、连接/模型管理、统一入口、连接测试根任务及 Worker、基础限额 | Key 不回显，私网绕过测试拒绝；429/无 usage/超时正确；测试任务可查询；并发预留和重复结算测试通过 |
| T04 智能体与聊天 Run | T03；草稿/revision/快照、会话、扩展 Worker 的聊天执行、图状态和业务投影 | 不同幂等请求互斥；旧版本不受草稿修改影响；崩溃后补投影不重新调用供应商 |
| T05 聊天与历史 | T04；SSE reducer、聊天/历史/取消/用量页面、删除清理 | 刷新和重连无重复文本；事件过期取快照续订；取消/失败保留片段；历史不调用模型 |
| T06 集成验收 | T05；接口契约、Playwright、故障矩阵、真实供应商验证 | 本节场景均有命令/结果；发现的问题修复后回归，未验证项单列 |
| T07 部署与交接 | T06；完整 Compose、secret 注入、备份/恢复、运维对账、接入 README | 干净环境启动并完成管理员到聊天流程；恢复后历史与凭据可用；记录试用容量，不夸大 SLO |

T03 的连接测试已经需要 Run、任务、事件及账本，因此 0003 必须先于 0004，不能等待 T04 才创建 execution 表。T04 复用根任务执行机制增加聊天闭环。首次跨模块闭环前，不能把任何单个页面完成当作任务组整体完成。

| 验收组 | 必须验证的场景 | 建议测试位置 |
| --- | --- | --- |
| A 身份 | 激活重复消费、MFA 重放、恢复码单次使用、CSRF、禁用/改密、最后管理员保护 | integration/auth，e2e/account |
| B 资源 | 两用户数据隔离、禁止客户端改归属、凭据轮换、草稿 revision 冲突、旧版本可追溯 | integration/resources |
| C 模型 | 固定协议、出站策略、无隐式重试、连接测试收费归属、usage 缺失 | integration/model_access，provider |
| D 任务 | 同会话并发、幂等重复/冲突、取消竞态、输入 checkpoint 已提交但索引滞后、结果投影失败 | integration/runs |
| E 账本 | 额度竞争、Redis 成功 DB 失败、结算重复、未知用量、月界冻结、Redis 重启恢复准入 | integration/metering |
| F 流式 | 重复事件、快照覆盖、游标过期、断线不取消、部分消息终态、慢订阅者 | frontend reducer 单测、e2e/chat |
| G 运维 | 空库迁移、重启续聊、会话删除、备份恢复、密钥恢复、日志脱敏 | integration/retention，部署演练 |

故障测试使用真实测试 PostgreSQL/Redis 和可控假供应商，不只 mock 业务函数。恢复类测试要统计供应商实际接收次数，证明未重复调用；账本测试核对记录与余额，不只检查 HTTP 状态。并发验证不要求部署高可用集群。

## 9. 本次验证与剩余实施工作

本次完成文档契约、后端依赖解析和前端候选元数据检查；没有创建 platform 代码、数据库或部署服务。当前工具环境为 Python 3.8.10，未发现 Node.js，因此未在本环境运行候选应用。T01 需准备指定运行时并完成真实兼容性检查，不得复用旧 Python 3.10 Dockerfile 作为新平台基线。

部署域名、真实模型地址/Key、目标机器容量属于环境输入，采用配置注入；缺少这些输入不妨碍用测试环境完成工程和逻辑验证，但真实供应商验证和正式部署不能凭空标记完成。依赖版本调整、容量结果和功能完成情况统一写入进度说明。
