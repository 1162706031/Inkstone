# 本地部署 PostgreSQL 和 Redis

这份指南用于在 Windows 开发机上从零启动 `platform/` 的数据服务。PostgreSQL 17 在 Windows 运行，Redis 7.4 在 WSL Ubuntu 运行，API 和 Worker 在 Windows 运行。两项服务均绑定回环地址，数据保存在重启后可以复用的目录中。

| 用途 | PostgreSQL | Redis |
| --- | --- | --- |
| 日常开发（本文示例） | `127.0.0.1:25432/ai_platform_dev` | `127.0.0.1:26379/0` |
| 窗口 2 临时验收 | `127.0.0.1:25479/ai_platform_test` | `127.0.0.1:26479/11` |

验收夹具会在测试库创建并清理业务表。日常使用请配置第一行。已有专用开发实例时，可以直接使用其实际地址和端口，从建库授权或应用配置步骤接入。

## 1. 启动独立的 PostgreSQL 17

先安装 Windows x64 的 PostgreSQL 17，包含 Server 和 Command Line Tools。在普通用户的 PowerShell 中执行以下命令。示例为平台创建独立数据目录，可与使用 5432 端口的其他项目并存。[Windows 安装包](https://www.postgresql.org/download/windows/) · [initdb 说明](https://www.postgresql.org/docs/17/app-initdb.html)

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\17\bin'
$devRoot = Join-Path $env:LOCALAPPDATA 'ai-platform-dev'
$pgData = Join-Path $devRoot 'postgres'
New-Item -ItemType Directory -Force $devRoot | Out-Null
& "$pgBin\postgres.exe" --version
# 仅首次执行。按提示为本开发集群的管理员设置密码并保存。
& "$pgBin\initdb.exe" -D $pgData -U postgres -A scram-sha-256 -W -E UTF8 --locale=C
```

看到初始化成功后启动服务。已有 `PG_VERSION` 的数据目录直接从此处启动。

```powershell
& "$pgBin\pg_ctl.exe" -D $pgData -l "$devRoot\postgres.log" -o "-h 127.0.0.1 -p 25432" -w start
& "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 25432
```

看到 `accepting connections` 后，继续第 2 节。第 3 节的统一脚本将创建开发角色、数据库和表。

## 2. 在 WSL 中启动独立的 Redis 7.4

在 PowerShell 用 `wsl -l -v` 查找 Ubuntu 发行版，再用 `wsl -d Ubuntu` 进入（按实际名称替换）。首次安装 WSL 可使用 `wsl --install -d Ubuntu`。以下命令在 **Ubuntu Bash** 中执行。有 Redis 7.4 可执行文件时，将其所在目录加入 PATH，并从配置步骤继续。

```bash
sudo apt-get update
sudo apt-get install -y build-essential curl ca-certificates
mkdir -p "$HOME/.local/src"
cd "$HOME/.local/src"
version=7.4.12
curl -fSLO "https://download.redis.io/releases/redis-$version.tar.gz" &&
  tar -xzf "redis-$version.tar.gz" &&
  cd "redis-$version" &&
  make -j2 MALLOC=libc &&
  make PREFIX="$HOME/.local" install
export PATH="$HOME/.local/bin:$PATH"
redis-server --version
```

首次配置时输入独立的 Redis 密码，建议用密码管理器生成至少 32 位随机字母数字，便于直接写入连接 URL。下面的命令隐藏输入，将密码写入仅当前 WSL 用户可读的配置文件。启用 AOF 后，Redis 每秒同步追加日志；聊天记录、账本和任务保存在 PostgreSQL。[Redis 源码发布包](https://download.redis.io/releases/) · [Redis 持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)

```bash
umask 077
redis_dir="$HOME/.local/share/ai-platform-dev/redis"
mkdir -p "$redis_dir/data"
if [ ! -e "$redis_dir/redis.conf" ]; then
  read -rsp '开发 Redis 密码（至少 32 位字母数字）: ' redis_pw
  printf '\n'
  if [[ "$redis_pw" =~ ^[A-Za-z0-9]{32,}$ ]]; then
    cat > "$redis_dir/redis.conf" <<EOF
bind 127.0.0.1
protected-mode yes
port 26379
dir "$redis_dir/data"
appendonly yes
appendfsync everysec
requirepass $redis_pw
EOF
  else
    echo '密码格式不符，请重新执行配置步骤。'
  fi
  unset redis_pw
fi
redis-server "$redis_dir/redis.conf"
```

保持这个 WSL 窗口运行。在另一个 Ubuntu 终端执行 `~/.local/bin/redis-cli -h 127.0.0.1 -p 26379 --askpass PING`，输入开发 Redis 密码，预期得到 `PONG`。使用系统安装的 Redis 时可直接调用 `redis-cli`。

## 3. 连接应用并初始化数据库

回到 **Windows PowerShell**，从仓库根目录进入后端。下面的命令只在文件缺失时创建开发配置和 32 字节加密密钥。后续启动继续使用同一密钥，以便解密已保存的供应商凭据。

```powershell
Set-Location platform/backend
uv sync --locked --group dev
$devRoot = Join-Path $env:LOCALAPPDATA 'ai-platform-dev'
$archiveDir = Join-Path $devRoot 'archive'
New-Item -ItemType Directory -Force $archiveDir | Out-Null
$keyPath = Join-Path $devRoot 'credential.key'
if (!(Test-Path -LiteralPath $keyPath)) {
    uv run --locked python -c "import secrets,sys; f=open(sys.argv[1],'xb'); f.write(secrets.token_bytes(32)); f.close()" "$keyPath"
}
if ((Get-Item -LiteralPath $keyPath -ErrorAction Stop).Length -ne 32) { throw '密钥文件长度应为 32 字节' }
if (!(Test-Path .env)) { Copy-Item ../infra/.env.example .env }
Write-Output $keyPath
Write-Output (Join-Path $archiveDir 'deletions.sqlite3')
```

编辑 `platform/backend/.env`，用开发角色 `ai_dev` 的密码和第 2 节设置的 Redis 密码和上一步输出的绝对路径替换占位符。Windows 文件路径可用正斜杠。密码包含 `@`、`:`、`/`、`#` 或 `%` 时，须对 URL 中的密码部分做百分号编码。API 与 Worker 都从 `platform/backend` 启动并读取这份配置。

```dotenv
ENVIRONMENT=development
APP_ORIGIN=http://127.0.0.1:5174
DATABASE_URL=postgresql://ai_dev:<数据库密码>@127.0.0.1:25432/ai_platform_dev
CHECKPOINT_DATABASE_URL=postgresql://ai_dev:<数据库密码>@127.0.0.1:25432/ai_platform_dev
REDIS_URL=redis://default:<Redis密码>@127.0.0.1:26379/0
REDIS_PREFIX=ai-dev
CREDENTIAL_KEY_FILE=C:/Users/<用户名>/AppData/Local/ai-platform-dev/credential.key
CREDENTIAL_KEY_VERSION=v1
DELETION_ARCHIVE_PATH=C:/Users/<用户名>/AppData/Local/ai-platform-dev/archive/deletions.sqlite3
```

**使用 Navicat 或 DBeaver**

1. 如果还没有开发库，以实例管理员创建可登录的角色 `ai_dev`，设置与 `.env` 相同的密码，然后新建数据库 `ai_platform_dev`，所有者选 `ai_dev`。
2. 以 `ai_dev` 重新连接 `ai_platform_dev`，在该库中选择“运行 SQL 文件”，以 UTF-8 执行整个 [sql/init.sql](sql/init.sql)。文件会创建业务表、图检查点和首个管理员。

**使用命令行从零建库**

在 `platform/backend` 运行下面的命令。脚本按需创建角色和数据库，切换连接后自动执行同一份 `init.sql`。首次创建 `ai_dev` 时按提示设置密码。

```powershell
& "$pgBin\psql.exe" -X -h 127.0.0.1 -p 25432 -U postgres -d postgres -f ../infra/bootstrap-dev.psql
```

**核对初始化结果**

已初始化的库先查询版本。以库所有者从当前版本按顺序补齐 [0006 成员删除](sql/upgrades/0006_member_deletion.sql)、[0007 共享智能体](sql/upgrades/0007_shared_agents.sql) 和 [0008 智能体发布](sql/upgrades/0008_agent_releases.sql)。最终版本为 `0008_agent_releases`。升级前创建的智能体转为私人草稿，历史会话和快照保留。首次发布后可继续使用，创建人或管理员可以调整可见性。

```sql
SELECT version_num FROM public.alembic_version;
```

首次登录使用 `admin` / `admin`，按页面提示改密并绑定 TOTP。

## 4. 验收、重启与排错

在后端目录启动 API，再从另一个 PowerShell 检查就绪接口。HTTP 200 和 `{"status":"ok"}` 表示 API 已能执行 PostgreSQL 查询及 Redis PING。随后按[平台启动步骤](../README.md#安装类型生成与构建)启动 Worker 和前端，在 `http://127.0.0.1:5174` 登录。

```powershell
# 终端一
uv run --locked uvicorn ai_platform.api.main:app --host 127.0.0.1 --port 8081
# 终端二
Invoke-RestMethod http://127.0.0.1:8081/api/v1/health/ready | ConvertTo-Json -Compress
```

日常关机前，先停止前端、API 和 Worker，在 Redis 前台窗口按 Ctrl+C，然后执行以下 PowerShell 命令。下次打开电脑，用相同数据目录启动 PostgreSQL，再在 Ubuntu 中用原 `redis.conf` 启动 Redis，即可继续使用原来的数据和账号。

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\17\bin'
$devRoot = Join-Path $env:LOCALAPPDATA 'ai-platform-dev'
$pgData = Join-Path $devRoot 'postgres'
& "$pgBin\pg_ctl.exe" -D $pgData -m fast -w stop
# 下次启动 PostgreSQL
& "$pgBin\pg_ctl.exe" -D $pgData -l "$devRoot\postgres.log" -o "-h 127.0.0.1 -p 25432" -w start
# 在 Ubuntu 中启动 Redis
# ~/.local/bin/redis-server ~/.local/share/ai-platform-dev/redis/redis.conf
```

| 现象 | 核对方法 |
| --- | --- |
| PostgreSQL 启动失败 | 查看 `$devRoot\postgres.log`，用 `Get-NetTCPConnection -State Listen -LocalPort 25432` 核对端口占用 |
| 迁移提示 schema 权限不足 | 用 `ai_dev` 连接 `ai_platform_dev`，检查数据库所有者和 `public` 的 CREATE 权限 |
| 后端报“对模式 app 的权限不足” | 旧开发库的表归 `postgres` 所有。以 `postgres` 连接 `ai_platform_dev` 执行 [fix_owner.sql](sql/fix_owner.sql) |
| WSL 内 PONG，Windows 就绪接口返回 503 | 在 Windows 执行 `Test-NetConnection 127.0.0.1 -Port 26379`，核对 WSL 本机端口转发，再检查后端工作目录、Redis 密码及 URL 编码 |
| API 就绪但任务一直排队 | 在后端目录启动 Worker，确认它与 API 读取同一份配置 |

生产采用独立私网或托管实例、专用迁移账号和运行账号、Redis 应用 ACL，并按实例要求校验 TLS 证书。生产删除归档的位置须覆盖部署恢复的故障范围。本页步骤面向单机开发，生产备份及恢复演练按 T07 执行。[实例接入与环境隔离](../../docs/design/ai-platform-refactor.md#p1-commands)
