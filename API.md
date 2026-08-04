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

### 获取单个项目详情
- `GET /projects/{project_id}`
- 返回完整项目信息，包含 `scenes`

### 提交设定交互
- `POST /projects/{project_id}/interact`

### 分析并推进提问流程
- `POST /projects/{project_id}/analyze`

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
- `POST /projects/{project_id}/versions/{version_id}/restore`，JSON 需 `confirm=true`

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
