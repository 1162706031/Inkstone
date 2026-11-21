# 部署、恢复与回退手册

本手册面向平台运维人员。应用 Compose 只运行反向代理（含前端静态资源）、API 和 Worker。PostgreSQL 17、Redis 7.4 和删除归档存储由运维独立提供。所有命令从 `infra/` 执行。

## 1. 部署前准备

| 项目 | 要求 |
| --- | --- |
| PostgreSQL | 数据库 `ai_platform`。迁移账号 `ai_migrate` 拥有该库，运行账号 `ai_runtime` 只有 `CONNECT`。远程实例走私网，DSN 带 `sslmode=verify-full` |
| Redis | 应用专用 ACL 用户，键前缀与 `REDIS_PREFIX` 一致，禁用 `@admin` 和 `@dangerous`。示例：`user ai_app on >密码 ~ai:* &* +@all -@admin -@dangerous +info`，并关闭 `default` 用户 |
| 删除归档目录 | 位于业务库备份之外的故障域，例如另一台主机导出的 NFS/SMB 共享或独立磁盘。整机恢复的部署必须用异机存储。目录需预先创建，容器用户 UID 10001 可写 |
| 凭据密钥 | 32 个原始随机字节，与数据库备份分开保管。丢失后无法解密模型 Key 和 TOTP |
| 证书 | 内网 CA 或公网证书的 PEM 文件。演练可用 Caddy 内部 CA |

建库授权示例（以实例管理员执行）：

```sql
CREATE ROLE ai_migrate LOGIN PASSWORD '...';
CREATE ROLE ai_runtime LOGIN PASSWORD '...';
CREATE DATABASE ai_platform OWNER ai_migrate;
REVOKE ALL ON DATABASE ai_platform FROM PUBLIC;
GRANT CONNECT ON DATABASE ai_platform TO ai_runtime;
\connect ai_platform
REVOKE ALL ON SCHEMA public FROM PUBLIC;
```

## 2. 配置与 secret

secret 以文件形式注入，挂载为容器内 `/run/secrets/<名称>`，不写入环境变量、镜像或日志。目录权限设为仅运维账号可读。

```text
secrets/
  database_url          postgresql://ai_runtime:...@db-host:5432/ai_platform?sslmode=verify-full
  database_url_migrate  postgresql://ai_migrate:...@db-host:5432/ai_platform?sslmode=verify-full
  redis_url             rediss://ai_app:...@redis-host:6379/0
  credential_key        32 字节原始密钥
```

URL 中密码含 `@ : / # %` 时须做百分号编码。非秘密配置写入 `deploy.env`：

```dotenv
APP_ORIGIN=https://ai.internal.example.com
REDIS_PREFIX=ai
CREDENTIAL_KEY_VERSION=v1
CONTEXT_LIMIT_TOKENS=32768
# 仅在模型端点为内网 HTTP 时列出精确 API 根地址，逗号分隔
# PRIVATE_MODEL_ALLOWLIST=
```

Compose 变量写入 `compose.env`，用 `--env-file` 传入：

```dotenv
APP_VERSION=p1-YYYYMMDD-<commit>
SITE_ADDRESS=ai.internal.example.com
TLS_MODE=/etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem
HTTPS_BIND=0.0.0.0
HTTPS_PORT=443
SECRETS_DIR=/srv/ai-platform/secrets
DEPLOY_ENV_FILE=/srv/ai-platform/deploy.env
DELETION_ARCHIVE_DIR=/mnt/ai-platform-archive
TLS_DIR=/srv/ai-platform/tls
```

`TLS_MODE=internal` 使用 Caddy 内部 CA，仅用于演练。`APP_ORIGIN` 必须与浏览器访问的源完全一致，非 443 端口需带端口。下文的 `C` 指 `docker compose --env-file /srv/ai-platform/compose.env`。

## 3. 构建镜像

镜像在 CI 或构建机上从已锁定的 `uv.lock` 和 `package-lock.json` 构建，构建上下文为仓库根目录。每次发布使用新标签，并记录镜像 ID 以便回退。

```sh
APP_VERSION=p1-YYYYMMDD-<commit> $C build
docker image inspect --format '{{.Id}}' ai-platform-backend:$APP_VERSION ai-platform-web:$APP_VERSION
```

## 4. 首次部署

先核对实例连通、备份任务已开启，并确认归档目录已挂载。使用运维机上的 PostgreSQL 17 客户端，以第 1 节的库所有者连接空库。下例使用 libpq 的环境变量或密码文件提供主机、密码和 TLS 配置。

```sh
PGCLIENTENCODING=UTF8 psql -X -U ai_migrate -d ai_platform -v ON_ERROR_STOP=on -f sql/init.sql
psql -X -U ai_migrate -d ai_platform -c 'SELECT version_num FROM public.alembic_version'  # 0008_agent_releases
$C run --rm ops ai-platform grant-runtime --role ai_runtime
$C run --rm ops ai-platform init-archive
$C up -d --wait
curl -fsS https://ai.internal.example.com/api/v1/health/ready
```

首次部署只把入口开放给执行初始化的运维人员。用 `admin` / `admin` 登录，改密后重新登录并绑定 TOTP，保存恢复码，然后开放工作台入口。

`ops` 服务使用迁移账号，只用于一次性命令。API 和 Worker 使用运行账号，只有 `app`、`graph` 中表的增删改查权限。

## 5. 升级

1. 构建新标签镜像，记录当前 `APP_VERSION` 作为回退目标。
2. `pg_dump` 一次业务库（见第 7 节）。
3. 以库所有者从现有版本按顺序补齐 [0006 成员删除](sql/upgrades/0006_member_deletion.sql)、[0007 共享智能体](sql/upgrades/0007_shared_agents.sql) 和 [0008 智能体发布](sql/upgrades/0008_agent_releases.sql)。已有智能体以私人草稿进入新版本，保留创建归属和历史会话。核对版本为 `0008_agent_releases` 后执行 `$C run --rm ops ai-platform grant-runtime --role ai_runtime`，再更新 `compose.env` 的 `APP_VERSION`。
4. `$C up -d --wait`，确认就绪接口和登录。

升级会中断在途对话。Worker 被替换时，进行中的 Run 由新 Worker 接管或进入 `interrupted`，已提交文本保留。

## 6. 应用回退

迁移版本不变时，把 `APP_VERSION` 改回上一标签并执行 `$C up -d --wait --no-build`。数据库、归档和 Redis 不需要改动。

结构版本发生变化时，停应用并执行发布说明中已验证的回退 SQL，或使用升级前的备份按第 8 节恢复。

## 7. 备份

| 对象 | 方式 | 保留 |
| --- | --- | --- |
| PostgreSQL（`app` 和 `graph`） | 每日 `pg_dump --format=custom`，或托管服务的 PITR | 30 天 |
| 删除归档 | 位于独立存储，按该存储的快照策略备份。不得用业务库备份覆盖 | 至少覆盖业务库备份保留期 |
| 凭据密钥 | 离线保管在密钥库，与数据库备份分开 | 长期 |
| Redis | 不备份，只保存限流和短期协调计数 | — |

```sh
pg_dump --format=custom --file=ai_platform-$(date +%F).dump "$MIGRATE_URL"
pg_restore --list ai_platform-$(date +%F).dump | grep -c "TABLE DATA"   # 核对 app 与 graph 均在
```

## 8. 恢复数据库

按以下顺序执行。恢复完成且删除归档重放成功之前，不开放工作台。

```sh
$C stop
# 1. 以实例管理员重建空库并授权，语句同第 1 节
# 2. 恢复备份
pg_restore --exit-on-error --no-owner --role=ai_migrate --dbname="$MIGRATE_URL" backup.dump
$C run --rm ops ai-platform grant-runtime --role ai_runtime
psql -X --dbname="$MIGRATE_URL" -c 'SELECT version_num FROM public.alembic_version'
# 3. 重放独立归档中的删除，归档不可达时命令以 2 退出，实例保持隔离
$C run --rm ops ai-platform replay-deletions
# 4. 开放
$C up -d --wait
```

`replay-deletions` 会隐藏并清理归档中已确认删除的会话，取消随备份恢复的在途 Run。备份之后提交但未进入归档的删除会重新可见，用户可以再次删除。

恢复后核对：管理员能用原 TOTP 登录，证明凭据可解密；成员能读取历史；模型连接测试通过；已归档删除的会话返回 404。

## 9. Redis 协调状态重建

Redis 数据丢失或换实例时，无需恢复数据。启动 API 和 Worker 后，每次准入从 PostgreSQL 的在途调用重新计算并写回限流和并发计数。登录失败计数随 Redis 清空归零。

```sh
$C restart api worker
redis-cli --user ai_app --askpass --scan --pattern 'ai:model:*'   # 第一次模型调用后重新出现
```

## 10. 对账

调用结果未知（`uncertain`）的预留会继续占用额度。确认供应商侧用量后执行：

```sh
$C run --rm ops ai-platform reconcile-attempt --attempt-id <UUID> --input-tokens N --output-tokens M --reason provider_usage_confirmed
```

只有拿到供应商无消耗证据时才能使用 `--reason provider_confirmed_no_consumption`，并输入 0 和 0。超时、缺少 usage 或用户取消都不能作为零消耗证据。

## 11. 日志与排错

容器日志使用 `local` 驱动，单文件 20 MB，保留 14 个文件。日志不记录聊天正文、密码或 secret。

| 现象 | 处理 |
| --- | --- |
| API 不健康 | `$C logs api`，核对 `database_url`、`redis_url` 文件和私网连通 |
| 登录返回 403 | `APP_ORIGIN` 与浏览器地址不一致 |
| 删除持续返回 503 | 归档目录未挂载、不可写或文件被替换。`$C exec worker ls -l /var/lib/ai-platform/archive` |
| 模型调用 `DEPENDENCY_UNAVAILABLE` | 凭据密钥文件或版本不匹配 |
| SSE 不推送 | 中间代理缓冲。Caddy 已关闭缓冲，前置负载均衡需同样关闭 |
