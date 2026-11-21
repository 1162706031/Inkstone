# 内部 AI 平台

代码结构、分层和关键机制见[项目框架](docs/ARCHITECTURE.md)，总体设计见[设计文档](docs/DESIGN.md)。

后端 API 和 Worker 共用 `ai-platform` Python 包。前端通过 Vite 代理访问账号、模型目录、供应商管理和用量 API。Worker 领取管理员发起的连接测试，写入根任务状态、调用尝试和用量账本。

## 运行时和实例

首次在 Windows 开发机搭建环境，请从[本地部署 PostgreSQL 和 Redis](infra/database-setup.md)开始。指南包含安装、建库授权、持久化目录、`.env` 配置、SQL 初始化及就绪检查。

- Python 3.12.16、uv 0.13.7、Node.js 24.22.3、npm 12.2.3。运行时版本见 `backend/.python-version` 和 `frontend/.node-version`。
- 开发 PostgreSQL 使用 `ai_platform_dev` 和开发专用账号。部署时 `DATABASE_URL` 和 `CHECKPOINT_DATABASE_URL` 指向同库，`REDIS_URL` 指向开发独立 Redis。
- 后端在 `backend` 下读取 `.env`。参照 [配置示例](infra/.env.example) 注入连接配置及密钥文件路径。密钥文件应包含 32 个原始随机字节，由部署环境保管。首次启动需要 PostgreSQL 和 Redis，生产 `APP_ORIGIN` 必须为 HTTPS。
- 恢复探针只读取进程环境中的 `TEST_DATABASE_URL`，并核对实际数据库为 `ai_platform_test`。测试账号需要 `CONNECT` 和 `CREATE` 数据库权限，以便建两个随机命名的探针 schema。探针结束时删除这两个 schema。
- 隔离 Redis 使用 `TEST_REDIS_URL`。测试和开发使用独立实例及凭据。远程实例的 DSN 按实例要求启用 TLS 并校验证书。凭据通过环境注入。

## 安装、类型生成与构建

首次生成锁文件时在后端运行 `uv lock`，在前端运行 `npm install`。后续使用锁文件安装。

从 `backend` 运行：

```sh
uv sync --locked --group dev
uv run --locked ai-platform export-openapi --output ../contracts/openapi.json
uv build --no-sources
# 仅首次建本地开发库，连接参数与本地部署指南一致
psql -X -h 127.0.0.1 -p 25432 -U postgres -d postgres -f ../infra/bootstrap-dev.psql
uv run --locked uvicorn ai_platform.api.main:app --host 127.0.0.1 --port 8081
```

另开终端在 `backend` 运行 Worker，使用 Ctrl+C 退出：

```sh
uv run --locked python -m ai_platform.worker
```

从 `frontend` 运行：

```sh
npm ci
npm run api:generate
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npm run dev
```

本地前端地址为 `http://127.0.0.1:5174`。浏览器验收使用 `npx playwright install chromium` 准备浏览器，再运行 `npm run test:e2e`，Playwright 自动启动 API 和 Vite。

首次登录管理员使用 `admin` / `admin`，随后须改密、重新登录并绑定 TOTP。激活令牌和密码重置令牌由管理员通过受控渠道交付，成员在对应页面粘贴。运维可在丢失管理员验证器及恢复码时运行 `uv run --locked ai-platform reset-admin-mfa --username admin`。

模型连接 HTTP 例外通过逗号分隔的 `PRIVATE_MODEL_ALLOWLIST` 配置，条目为精确的 API 根地址，含方案、主机、端口和路径。测试供应商使用隔离环境中的回环地址。每次派发都解析并固定目的 IP、保留原主机的 TLS 校验并禁止重定向。生产禁止回环 HTTP。

## 行为探针

从 `backend` 运行：

```sh
uv run --locked pytest -q -rs tests/unit tests/contract
uv run --locked pytest -q -s tests/integration/test_fake_provider.py
uv run --locked pytest -q -rs -s tests/integration/test_recovery.py
```

T02 和 T03 使用空白隔离的 `ai_platform_test`，测试从默认 SQL 脚本创建对应版本的业务表，并在结束后清理。提供 `TEST_REDIS_URL` 时使用随机 Redis 键前缀验证真实限流和在途任务恢复；缺少时身份和模型请求使用限流/准入替身。

```sh
uv run --locked pytest -q -rs -s tests/integration/test_identity.py
uv run --locked pytest -q -rs -s tests/integration/test_models_metering.py
```

假供应商只监听本机随机端口。探针逐场景输出实际 HTTP 请求数和 usage 条数。PostgreSQL 探针通过独立连接模拟重启与接管，验证两轮输入 ID、输入游标、过期执行写入拒绝及事务回滚。缺少 `TEST_DATABASE_URL` 时 pytest 会报告跳过，验收时应看到实际 PostgreSQL 版本和探针断言输出。

已提供隔离 Redis 凭据时，可运行只读连通性和版本检查：

```sh
uv run --locked python -c "import os, redis; r = redis.Redis.from_url(os.environ['TEST_REDIS_URL'], socket_connect_timeout=5, socket_timeout=5); assert r.ping(); print(r.info('server')['redis_version'])"
```

## 对话工作台与测试归档

当前数据库版本为 `0008_agent_releases`。在 `/agents` 创建智能体时默认为私人草稿。保存后点击「发布」生成 v1，修改并重新发布后版本号递增。新会话使用最近一次发布的配置，已有会话固定使用创建时的版本。

公开智能体允许所有已登录成员查看、编辑、发布和使用。私人智能体仅创建人和管理员可见。创建归属始终保留，可见性和归档由创建人或管理员管理。可见性在保存时生效，改为私人后，其他成员仍可在 `/chat` 查看自己的历史，但无法继续调用该智能体。

API 与 Worker 的 `DELETION_ARCHIVE_PATH` 指向同一个独立持久卷上的 SQLite 文件绝对路径，父目录应已创建并允许写入。测试可使用 `infra/.test-archive/deletions.sqlite3` 的绝对路径。SQLite 以 FULL 同步提交，Worker 每分钟重试待确认标记，随后清理会话正文。该文件应独立于业务库及图的备份和还原。

在空白专用 `ai_platform_test`、隔离 Redis 和已构建前端上运行以下验收。设置 `TEST_DELETION_ARCHIVE_PATH` 可将测试中的已确认标记持久保留供窗口 3 复核。浏览器夹具使用本地动态端口、真实 API/Worker 和可控供应商。

```sh
# frontend
npm run build
# backend
WORKBENCH_BROWSER=1 uv run --locked pytest -q -rs -s tests
```

真实供应商验证默认跳过，需要提供获授权的测试凭据：

```sh
RUN_PROVIDER_TESTS=1 PROVIDER_TEST_BASE_URL=https://.../v1 PROVIDER_TEST_MODEL=... PROVIDER_TEST_API_KEY=... \
  uv run --locked pytest -q -s tests/provider
```

## 部署

应用 Compose、HTTPS、secret 注入、备份恢复、Redis 重建、删除归档重放和回退步骤见[部署手册](infra/deploy.md)。

