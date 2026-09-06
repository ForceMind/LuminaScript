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
- `services/setup_fields.py`：纯字段安全、标题、单位与完整设定校验；输入、AI 输出和最终确认共享规则，局部修复不改写锁定字段。
- `services/job_queue.py`：任务入队、原子领取、租约心跳、失败重试和超时回收。
- `worker.py`：独立生成进程；API 仅写入任务，不承载长时间生成。
- `repositories/`：数据库原子更新、项目生成抢占和 Token 累计。
- `migrations/`：Alembic 版本化迁移；旧 SQLite 首次运行会兼容升级并盖章。
- `main.py`：当前仍承载项目工作流和 LLM 生成引擎，后续继续按领域拆分。

## 基础设定并发边界

项目正式设定和交互缓存使用独立的单调版本，接口以 `context_revision` 同时绑定两者。
所有设定写入通过数据库条件更新：校验预期版本和没有活动生成任务后才允许提交；
过期请求返回冲突，不以 ORM 会话中缓存的旧对象覆盖新状态。用量累计独立于设定版本。

AI 调用只持有输入与版本快照，不在等待上游期间持数据库行锁。结果返回后再有条件地
写入缓存，只有仍匹配当前模式、流程和版本的缓存可以恢复。项目 GET 保持只读。
前端同样固定每次异步流程的项目、版本和请求序号，旧回包不得污染新项目或释放新请求的 loading 状态。

单项选项生成统一收集规范化后的合格值，最多两次语义调用，仅补缺额；普通引导与快速
模式共用该收集边界。模型解析错误保留已知用量和原始响应供审计，不能因 JSON 失败而
把已经发生的消耗记为零。前端仅显示规范值，不猜测或截断单位。

新增版本字段通过 Alembic 迁移；旧无版本数据库的识别标记保留历史迁移号，不能把尚未
包含新列的旧库直接盖章为最新版本。升级不批量改写用户作品；旧浏览器 token 需刷新。

## 工作稿与正式设定

`Project.quick_setup_draft` 独立保存工作稿、生成基线、手改/AI 来源、基准正式版本和保存时间。
保存仅推进缓存版本；采用草案才更新正式设定。模式切换不擅自把工作稿变成已确认答案。
正式内容变化后保留旧稿，但按基准版本判为只读过期稿，不提供隐式自动合并。

前端分别维护生成基线和最近保存值：前者用于 AI 的 before/after 方向分析，后者用于未保存
改动判断。保存响应只能确认发起时的快照，不能覆盖请求期间新增的本地编辑。离开保护与
删除、权限失效等强制清理分开处理，避免过期状态触发保存循环。

## 数据库迁移

AI 审计将操作者与计费主体分开存储：`user_id` 保留行为归属，nullable `billed_user_id`
表示新记录的计费用户，历史记录通过 `COALESCE(billed_user_id, user_id)` 兼容统计。
两条用户外键的 ORM 关系显式指定字段，避免关联歧义；导入使用用户 ID 映射同时恢复两者。
配额身份以请求/任务局部作用域传到模型调用边界，不使用可被其他用户请求覆盖的全局用户状态。
检查发生在每次新调用前，AI 等待期间不持数据库锁；已在途请求采用软额度语义。

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

### 生成准入、恢复与租约围栏

分场任务仅在正式设定已确认、设定字段完整且客户端 `context_revision` 与当前双版本一致时入队。已有场次只有明确的全量重新生成动作会清空；自动重试从已存在的场次序号继续，不删除已完成场次、正文或摘要。

Worker 在每次 provider 调用前后和每段业务写入前检查 `GenerationJob` 的 `RUNNING` 状态、租约 token 及取消标志。业务写入通过短事务条件更新围栏，provider 等待期间不持有数据库写锁。任务取消或租约失效不能终止已经发出的 provider HTTP 请求，但其返回结果只可作为 stale 审计记录，不能改写项目、场次或触发后续调用。

已知 provider usage 在活跃租约下先累计到项目并写入审计；租约失效后只尝试独立 stale 审计。若该审计存储也失败，错误明确要求按上游账单对账，不声称本地已保留 usage。

## 后续生产增强

- 生产数据库迁移到 PostgreSQL，并为多 Worker 领取任务引入
  `FOR UPDATE SKIP LOCKED` 优化吞吐。
- 接入指标与告警：队列长度、等待时间、重试次数、LLM 延迟和失败率。
- 将 `main.py` 中项目工作流与生成引擎继续拆到独立 service/use-case 模块。
- 当任务规模需要独立消息基础设施时，再评估 Redis + Celery/Dramatiq；
  数据库任务表继续作为业务审计与最终状态来源。
