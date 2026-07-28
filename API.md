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
