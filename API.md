# 妙笔流光 API 文档

## 认证
### 注册普通账户
- `POST /auth/register`
- JSON 参数：`username`、`password`
- 新账户默认没有管理员权限

### 登录
- `POST /token`
- 表单参数：`username`、`password`
- 返回：Bearer Token

### 当前用户
- `GET /users/me`
- 需要认证

### 修改当前用户密码
- `POST /users/me/password`
- JSON 参数：`current_password`、`new_password`
- 需要认证

## 项目接口
### 创建项目
- `POST /projects/`

### 获取项目列表
- `GET /projects/`
- 返回轻量列表，不包含分场内容
- 包含 `access_role`：`owner`、`editor` 或 `viewer`
- 项目响应包含 `setup_revision`、`setup_cache_revision` 和 `context_revision`。
- `has_quick_setup_draft`、`quick_setup_draft_stale` 表示是否有已保存稿及其是否过期；列表不携带完整工作稿。

### 获取单个项目详情
- `GET /projects/{project_id}`
- 返回完整项目信息，包含 `scenes`
- 详情包含 `quick_setup_draft`（可空），用于恢复工作稿；它独立于正式 `global_context`。

### 分析并推进提问流程
- `POST /projects/{project_id}/analyze`
- 新项目首先返回 `setup_mode`，可选 `ai_fast`（AI 快速完成）或 `guided`（自己掌控）
- `ai_fast` 会返回 `quick_review` 完整草案；草案在确认前只缓存，不写入项目正式设定
- 响应顶层及交互 payload 返回最新 `context_revision`；缓存变化也会推进版本，客户端应采用最新返回值。

### 设定版本与写入冲突
- `context_revision` 使用 `setup-v2:S:C` 格式，分别绑定正式设定版本和草案/提问缓存版本。
- 修改设定、切模式、重置/回退、确认、项目类型 PATCH 和版本恢复均需发送当前 token。
- 缺失、旧格式或过期 token 返回 `409`；必须重新获取项目/交互状态，不能去掉版本字段重试。
- 同一版本的并发写入只允许一方成功；活动生成任务期间拒绝修改基础设定。
- Token 用量累计不改变设定版本。读取项目不再隐式持久化标题或上下文规范化结果。

### 提交单项设定或切换设定方式
- `POST /projects/{project_id}/interact`
- 切换方式时使用 `context_key=setup_mode`，`answer=ai_fast|guided`
- JSON 同时携带 `context_revision`。

### 修改项目类型
- `PATCH /projects/{project_id}`，JSON 为 `project_type` 和 `context_revision`。
- 成功后采用响应中的最新版本；与旧类型关联的缓存不再沿用。

### 确认 AI 快速设定草案
- `POST /projects/{project_id}/setup/quick-review`
- `action=confirm`：校验并原子写入整份草案
- `action=guided`：丢弃未确认草案并切换到逐步掌控
- 请求必须携带草案返回的 `context_revision`；项目已被其他标签页修改时返回 `409`

### 保存、恢复与丢弃工作稿
- 同一 quick-review 接口新增 `save`、`save_guided`、`discard`、`regenerate` 动作。
- 请求包含 `values`、`baseline_values`、`edited_fields`、`ai_adjusted_fields`、`context_revision`。
- `save` 返回 `status=saved`：只保存工作稿并推进缓存版本，不写正式标题/类型/确认标志。
- `save_guided` 返回 `status=saved_guided`：同一次原子操作保存稿并切换逐步掌控；保存稿仅供恢复，不自动成为逐项已确认答案。
- `discard` 返回 `status=discarded`：明确删除已保存工作稿；`regenerate` 返回 `status=regenerate`：清除工作稿和旧生成缓存，准备重新生成快速草案，保留正式已确认约束。
- 工作稿结构为 `schema=1`、`values`、`baseline_values`、两组改动字段、`base_setup_revision` 和 `saved_at`。再次保存不得替换原生成基线。
- 单纯切模式会保留有效稿的可恢复性；正式内容变化后旧稿过期，只可查看、复制和明确丢弃，不自动合并或覆盖。
- `/analyze` 优先恢复保存稿。quick-review payload 的 `draft_status` 为 `generated|saved|stale`，并包含 `draft_stale`、`read_only`、工作稿值和来源元数据；顶层 `saved_draft_available` 提示逐步模式可以返回快速审查。
- 过期稿不能保存、确认或调用单项/联动 AI。客户端应展示只读冲突状态，不给旧稿换上新 token 后继续写入。

### 单项重生与 AI 复核
- `POST /projects/{project_id}/setup/quick-review/ai-revise`
- 共同请求字段：`values`（完整当前草案）、`baseline_values`、`edited_fields`、`ai_adjusted_fields`、`context_revision`、可选 `instruction`。
- `operation=regenerate_field` 需 `target_field`；成功返回 `status=options` 和三个 `label/value` 选项，只供客户端选择，不写正式设定。
- `operation=review_edits` 使用 `scope=edited_only|related`；成功返回 `status=candidate`、`changes` 和摘要，应用前须由用户确认。
- 改动以当前值相对基线的真实差异计算；基线优先使用已保存稿或有效服务器缓存。`edited_only` 只优化已改内容项；`related` 锁定用户/AI 明确选择的改后值与类型/规模，只修改必要关联内容。
- 仅选择 AI 方案后也可发起关联复核；尺度变化作为锁定约束参与分析，不由联动 AI 改回原值。没有可修改项时不额外调用模型。
- 返回 `tokens_used`、`total_tokens` 和版本；过期请求不得覆盖更新后的设定或缓存。
- 生成期间本地草案再次变化时，客户端应拒绝应用旧候选。

### 字段校验与三选一补齐
- 请求仍限制字段白名单、单字段 20,000 字和总量 60,000 字；未知字段或超长输入不能调用 AI。
- 单项重生允许目标值为空或无效，锁定的其他字段不被自动改写；最终确认才要求整份必填设定有效。
- `user_notes` 可为空或“无”，最终规范为“无”；其他必填内容不因此放宽。
- 场次、集数只接受正整数。时长识别秒、分钟、小时及对应英文单位；裸数使用字段默认单位。
- 电影时长按分钟数字保存，单集时长按分钟字符串保存（例如 `1.5mins`），短视频按整数秒保存。
- 示例：单集 `90秒` → `1.5mins`，电影 `1.5小时` → `90`，短视频 `2分钟` → `120`。负数、范围、多组数值及不能精确转换的输入明确报错，不截取第一组数字或四舍五入。
- 单项选项统一规范化后去除旧值、重复和非法项，保留合格项；不足三个时最多追加一次定向补齐。两次语义生成后仍不足则返回失败，不用占位项凑数。
- 所有已知模型用量累计，包括格式失败、补齐、被丢弃和过期结果；底层网络重试仍遵循既有上限。
- 审计存储故障时明确返回 `503`，不将未能记录的 AI 结果报告为成功或应用到草案；已知项目用量保留，错误信息说明原始响应尚未保存，不自动再调用模型。

### 生成分场大纲
- `POST /projects/{project_id}/generate_scenes`
- 返回 `job_id`；任务写入持久化队列后由独立 Worker 执行
- 同一项目已有活动任务时返回 `409`

### 重新生成单场
- `POST /projects/{project_id}/scenes/{scene_index}/regenerate`
- 返回 `job_id`；重新生成任务由独立 Worker 执行

### 导出单个项目
- `GET /projects/{project_id}/export?format=txt|md|docx`

## 管理员接口
以下接口需要管理员权限。

### 用户列表
- `GET /admin/users`

### 设置用户角色
- `PATCH /admin/users/{user_id}/role`
- JSON 参数：`is_admin`
- 不能取消自己的管理员权限，系统至少保留一名管理员

### AI 配置
- `GET /admin/ai-config`：读取当前配置（API Key 仅返回是否已配置及掩码）
- `PUT /admin/ai-config`：更新 Base URL、模型 ID、API Key、接口协议、超时和并发数
- `POST /admin/ai-config/test`：使用提交的配置测试连接，不保存配置
- `POST /admin/ai-config/models`：使用提交或已保存的密钥读取上游 `/v1/models`
- API Key 留空表示保留现有密钥；`clear_api_key=true` 表示清除密钥
- 模型列表请求必须至少提交 `api_key` 或 `profile_id`；显式 `api_key` 优先，
  `profile_id` 用于从安全存储读取对应密钥
- AI 上游失败统一返回 `{"error":{"code":"...","message":"..."}}`；上游参数、
  认证、连接与超时错误分别使用合适的 4xx、502 或 504，响应和日志不会包含完整密钥

### 多 AI 档案与任务路由
- `GET /admin/ai-profiles`：读取档案、默认档案和任务路由
- `PUT /admin/ai-profiles/{profile_id}`：新增或更新档案
- `DELETE /admin/ai-profiles/{profile_id}`：删除档案（至少保留一个）
- `PUT /admin/ai-routing`：设置默认档案及各任务的候选档案顺序
- AI 配置中的 `api_protocol` 可选 `chat_completions`（默认）或 `responses`
- AI 配置中的 `stream_response=true` 表示上游仅接受流式响应；服务端会聚合 SSE 分片

### 运维、备份和配额
- `GET /admin/ops/jobs`：最近 200 个生成任务
- `POST /admin/ops/jobs/{job_id}/cancel`
- `POST /admin/ops/jobs/{job_id}/retry`
- `GET /admin/ops/alerts`：各状态数量及最近失败任务
- `GET|PUT /admin/ops/backup-settings`
- `POST /admin/ops/backups`：立即创建服务器备份
- `GET /admin/ops/backups`：备份列表
- `GET /admin/ops/backups/{backup_id}/download`
- `POST /admin/ops/backups/{backup_id}/restore`：以副本方式恢复项目，JSON 需 `confirm=true`
- `GET /admin/ops/usage`：所有用户的当日/当月用量及额度
- `PATCH /admin/ops/users/{user_id}/quota`：设置每日/月度 Token 额度；`0` 表示不限

### Prompt 模板管理
- `GET /admin/ops/prompt-templates`
- `POST /admin/ops/prompt-templates`
- `PUT|DELETE /admin/ops/prompt-templates/{template_id}`
- 阶段：`outline`、`content`、`review`、`interaction`、`prompt`

## 项目工具接口
以下接口需要项目访问权限；写操作要求 `editor` 或 `owner`。

### 版本
- `GET|POST /projects/{project_id}/versions`
- `GET /projects/{project_id}/versions/{version_id}/diff`
- `POST /projects/{project_id}/versions/{version_id}/restore`，JSON 需 `confirm=true` 和 `context_revision`；活动生成任务或版本冲突时拒绝恢复。

### 协作成员
- `GET|POST /projects/{project_id}/members`
- `PATCH|DELETE /projects/{project_id}/members/{member_id}`
- 只有所有者可管理成员；角色为 `viewer` 或 `editor`

### 任务与用量
- `GET /jobs?project_id={project_id}`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/{job_id}/retry`
- `GET /usage/me`
- `GET /prompt-templates?stage=content&project_type=movie`

### 登录日志
- `GET /admin/logs/login?page=1&page_size=20`

### AI 审计日志
- `GET /admin/logs/ai?page=1&page_size=20`

### 导出全部用户数据
- `GET /admin/export/all`
- 返回：ZIP 文件
- 包含：
  - 用户列表
  - 项目与分场数据
  - 登录日志
  - AI 日志
  - SQLite 数据库文件备份（如果存在）

## 内容审核接口
### 审核并生成安全改写建议
- `POST /content/review`
- 用于检查用户自由输入中的不当内容，并在需要时给出 AI 改写建议

## 运维脚本
### 部署
```bash
bash deploy.sh
```

### 更新
```bash
bash update.sh
```

### 卸载
```bash
bash uninstall.sh
```

### 运维命令
```bash
miaobi
miaobi status
miaobi stop
miaobi logs worker
```
