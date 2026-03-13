# 妙笔流光 API 文档

## 认证
### 登录
- `POST /token`
- 表单参数：`username`、`password`
- 返回：Bearer Token

### 当前用户
- `GET /users/me`
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

### 重新生成单场
- `POST /projects/{project_id}/scenes/{scene_index}/regenerate`

### 导出单个项目
- `GET /projects/{project_id}/export?format=txt|md|docx`

## 管理员接口
以下接口需要管理员权限。

### 用户列表
- `GET /admin/users`

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
```