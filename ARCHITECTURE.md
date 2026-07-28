# LuminaScript 架构

## 当前模块边界

```mermaid
flowchart LR
    UI["Vue 前端"] --> API["FastAPI"]
    API --> Auth["api/auth_routes.py"]
    API --> Admin["api/admin_routes.py"]
    API --> Project["main.py 项目与生成路由"]
    Project --> Queue["generation_jobs 持久化队列"]
    Worker["worker.py"] -->|原子领取 / 心跳 / 重试| Queue
    Worker --> LLM["LLM 服务"]
    Auth --> Services["services"]
    Admin --> Services
    Project --> Services
    Project --> Repositories["repositories"]
    Services --> DB["SQLAlchemy Async"]
    Repositories --> DB
    DB --> SQLite["SQLite（本地）"]
    DB --> PostgreSQL["PostgreSQL（生产可选）"]
    Alembic["Alembic"] --> DB
```

- `core/config.py`：唯一配置入口，负责类型、范围和数据库地址规范化。
- `api/`：HTTP 路由与权限依赖，不执行运行时 DDL。
- `services/`：登录限流、审计、管理员初始化和生成状态规则。
- `services/job_queue.py`：任务入队、原子领取、租约心跳、失败重试和超时回收。
- `worker.py`：独立生成进程；API 仅写入任务，不承载长时间生成。
- `repositories/`：数据库原子更新、项目生成抢占和 Token 累计。
- `migrations/`：Alembic 版本化迁移；旧 SQLite 首次运行会兼容升级并盖章。
- `main.py`：当前仍承载项目工作流和 LLM 生成引擎，后续继续按领域拆分。

## 数据库迁移

```bash
cd backend
python migrate.py
```

部署、更新和 Windows 启动脚本都会在服务启动前自动运行该命令。生产环境可设置：

```dotenv
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/luminascript
```

相对 SQLite 地址始终以 `backend/` 为基准，避免启动目录改变数据库目标。

## 持久化任务队列

```mermaid
flowchart LR
    API["API"] -->|写入任务| Queue["generation_jobs"]
    Worker["独立 Worker"] -->|原子领取| Queue
    Worker --> LLM["LLM 服务"]
    Worker --> ProjectDB["项目 / 场次状态"]
    API -->|查询进度| ProjectDB
```

任务状态统一为 `queued → running → completed/failed`，并记录尝试次数、
租约时间、租约所有者令牌和最后错误。心跳、完成与失败写入都必须匹配令牌，
避免过期 Worker 覆盖新持有者。Worker 崩溃后由租约回收任务，API 重启不会丢失任务。
生产建议 PostgreSQL；需要横向扩容时再引入 Redis 队列，避免当前阶段增加双写。

Worker 可独立运行：

```bash
cd backend
python worker.py
```

部署脚本会创建 `lumina-worker` systemd 服务；`miaobi status`、`start`、
`stop`、`restart` 和 `logs worker` 均会管理该进程。可通过
`WORKER_POLL_SECONDS` 和 `WORKER_LEASE_SECONDS` 调整轮询与租约。

## 后续生产增强

- 生产数据库迁移到 PostgreSQL，并为多 Worker 领取任务引入
  `FOR UPDATE SKIP LOCKED` 优化吞吐。
- 接入指标与告警：队列长度、等待时间、重试次数、LLM 延迟和失败率。
- 将 `main.py` 中项目工作流与生成引擎继续拆到独立 service/use-case 模块。
- 当任务规模需要独立消息基础设施时，再评估 Redis + Celery/Dramatiq；
  数据库任务表继续作为业务审计与最终状态来源。
