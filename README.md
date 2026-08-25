# 妙笔流光 (LuminaScript)

妙笔流光是一个 AI 辅助剧本创作平台，支持从创意输入、设定补全、分场大纲生成，到逐场剧本内容创作的完整流程。

## 技术栈
- 前端：Vue 3、Vite、Element Plus、Tailwind CSS
- 后端：FastAPI、SQLAlchemy Async、SQLite
- AI：兼容 OpenAI 接口的模型服务

## 目录结构
```text
LuminaScript/
├─ backend/                     后端服务
│  ├─ api/                      认证与管理员 HTTP 路由
│  ├─ core/                     统一配置
│  ├─ repositories/             数据访问与原子更新
│  ├─ services/                 领域服务与 LLM
│  ├─ migrations/               Alembic 数据库迁移
│  └─ worker.py                 持久化生成任务 Worker
├─ frontend/                    前端应用
├─ deploy.sh                    Linux 一键部署脚本
├─ update.sh                    Linux 一键更新脚本
├─ uninstall.sh                 Linux 卸载脚本
├─ miaobi                       终端运维命令
├─ README.md                    使用说明
├─ ARCHITECTURE.md              架构与演进方案
└─ API.md                       API 与管理端接口说明
```

## 本地开发
### Windows
1. 安装 Python 3.10+ 和 Node.js 18+
2. 在项目根目录运行 `start.bat`
3. 首次运行按提示设置引导管理员，并补充 `backend/.env` 中的模型配置

系统不会创建默认密码。普通用户从登录页注册账户；首位管理员建立后，可在“系统后台管理 → 用户管理”中把已注册账户设为管理员。

默认地址：
- 前端：`http://localhost:5173`
- 后端：`http://127.0.0.1:8000`

## 服务器部署
### 首次部署
```bash
cd /root/LuminaScript
chmod +x deploy.sh update.sh uninstall.sh miaobi
sudo bash deploy.sh
```

部署完成后会自动：
- 安装依赖
- 构建前端
- 生成强随机 `SECRET_KEY`
- 引导创建或更新首位管理员
- 启动后端、生成 Worker 与前端服务
- 写入运行信息文件 `.lumina_runtime`
- 安装全局运维命令 `miaobi`

## 日常运维
### 推荐入口
```bash
miaobi
```

这会打开中文终端运维面板。

### 常用命令
```bash
miaobi status          # 查看前后端状态与端口
miaobi start           # 启动服务
miaobi stop            # 停止服务
miaobi restart         # 重启服务
miaobi logs backend    # 查看后端日志
miaobi logs frontend   # 查看前端日志
miaobi update          # 执行更新脚本
miaobi uninstall       # 执行卸载脚本
```

### 停止服务命令
```bash
miaobi stop
```

## 更新
```bash
cd /root/LuminaScript
bash update.sh
```

更新脚本会：
- 检查 Git 工作区，避免覆盖人工改动
- 自动备份数据库、环境变量、日志和运行信息
- 拉取最新代码
- 更新后端依赖
- 执行 Alembic 数据库迁移
- 重新构建前端
- 重启服务
- 重新安装 `miaobi`

## 卸载
```bash
cd /root/LuminaScript
bash uninstall.sh
```

卸载脚本会：
- 先备份数据库、环境变量和日志
- 停止前后端服务
- 移除 `miaobi` 命令入口
- 按提示决定是否删除虚拟环境、前端构建产物、日志、数据库和配置文件

默认不会直接删除整个项目目录，也不会强制删除用户数据。

## 管理后台
管理员登录后可进入“管理后台”，支持：
- 用户列表查看与管理员角色设置
- 管理多套 OpenAI 兼容 AI 配置，按大纲、正文、审核等任务路由并自动故障切换
- 监控、取消和重试生成任务，查看失败告警
- 设置用户每日/月度 Token 额度并查看用量
- 管理按项目类型和生成阶段生效的 Prompt 模板
- 创建、定时保留和下载加密服务器备份，可镜像到已挂载的 NAS/云盘目录
- 登录日志查看
- AI 审计日志查看
- 一键导出全部用户数据

项目编辑页的“项目工具”还支持：
- 手动版本快照、版本差异查看和恢复；重新生成前会自动建立快照
- 以只读或可编辑角色共享项目
- 查看、取消和重试当前项目的生成任务
- 查看当前账户的 Token 用量与额度

### 长篇故事连续性
大纲与正文生成都会携带有长度上限的“故事圣经”、关键里程碑、最近场次和
上一场真实结尾，并明确传递当前绝对场次及剧情阶段。第 8 场以后若输出疑似
重新开篇，连续性守卫会自动重写一次；仍疑似重启时会拒绝该结果并把任务标记
为失败，避免错误正文继续污染后续场次。

### 一键导出全部用户数据
管理员面板中的“导出全部用户数据”会下载一个 ZIP，包含：
- `manifest.json`
- `users.json`
- `projects.json`
- `login_logs.json`
- `ai_logs.json`
- 数据库一致性快照（如果当前环境是 SQLite，包含尚在 WAL 中的数据）

### AI 配置管理
管理员可在“管理后台 → AI 配置”中维护多套 Base URL、模型 ID、API Key、
接口协议、请求超时和最大并发数，并为大纲、正文、策划、交互、审核和提示词任务指定
首选模型。调用失败时会按路由及优先级切换到下一套可用配置。保存后无需重启
服务，后端 API 与独立生成 Worker 会从下一次请求开始使用新配置。API Key
不会通过管理接口返回明文。档案可分别选择 Chat Completions 或 Responses API；
Codex 渠道提示 `/v1/chat/completions endpoint not supported` 时应选择 Responses API。
“获取模型”会使用当前填写或已安全保存的 Key 请求上游 `/v1/models`，成功后可直接
搜索和选择模型；不提供模型列表接口的服务仍可手动输入模型 ID。
对于只接受 SSE 流式输出的自建服务，可启用“仅流式响应”，系统会在服务端聚合
Chat Completions 分片或 Responses 语义事件后交给原有生成流程。

### 故事设定方式
新建创意后可选择“AI 快速完成”或“自己掌控”。快速模式会联合生成一份内部一致的
完整故事设定，并在折叠审查页中默认采用；用户可以只展开有疑问的字段修改，确认后
整份草案才会原子写入项目。逐步模式保留原有逐项选择流程，过程中也可随时把剩余
内容交给 AI。旧项目默认沿用逐步模式，不会因升级改变已有设定。

不希望在命令或聊天中暴露 API Key 时，可使用交互式连接测试工具：

```bash
# Windows：双击 test-ai-connection.bat
# Linux：
bash test-ai-connection.sh
```

工具会隐藏读取 Key（不写入文件），列出全部可用模型，并分别测试 Chat Completions
和 Responses API 的流式与非流式模式，最后只在检测到有效组合时打印可直接填写到
管理后台的完整配置和非敏感 JSON。

### 加密服务器备份
“管理后台 → 运维中心”可立即创建备份，也可按小时间隔自动创建。备份包含
项目与场次、协作成员、版本、Prompt 模板、任务、用量相关审计日志；SQLite
环境还包含一致性数据库快照。恢复默认创建“恢复副本”，不会覆盖现有项目。
加密密钥由服务器 `SECRET_KEY` 派生，因此轮换 `SECRET_KEY` 前必须保留旧密钥，
否则旧的 `.zip.enc` 备份无法解密。

## 运行文件说明
- `.lumina_runtime`：运行信息文件，记录当前前后端端口、日志路径、项目目录
- `backend/.llm_runtime.json`：管理后台保存的 AI 运行时配置（敏感文件，权限为 600）
- `backend/.backup_runtime.json`：定时、保留、加密和异地镜像备份设置（权限为 600）
- `backend.log`：后端运行日志
- `worker.log`：持久化生成任务 Worker 日志
- `frontend.log`：前端运行日志
- `backups/`：更新/卸载快照及 `backups/server/` 中的服务器内容备份

## 数据库迁移

部署与更新脚本会自动执行迁移。需要手工执行时：

```bash
cd backend
python migrate.py
```

旧版本若因并发生成留下相同 `project_id + scene_index` 的重复场次，升级程序会
保留内容最完整的一条，将其他原始记录写入 SQLite 表
`scene_duplicate_archive`，再建立唯一索引。该过程会在迁移前自动执行，无需
手工删除剧本内容。

本地默认使用 SQLite；生产可通过 `DATABASE_URL` 配置
`postgresql+asyncpg://...`。生成请求会先持久化到 `generation_jobs`，
再由独立 Worker 原子领取，因此 API 重启不会丢失已入队任务。详细模块边界见
[`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 安全建议
- 系统不提供默认管理员密码，管理员密码至少 10 个字符
- `SECRET_KEY` 缺失或不安全时部署脚本会自动生成；后端发现弱密钥会拒绝启动
- 后端默认只监听 `127.0.0.1`，公网访问应通过配置 TLS 的反向代理
- 管理员可在个人菜单中修改自己的密码
- 定期执行管理员数据导出并离线备份
- 对外开放端口前，确认安全组和防火墙规则
