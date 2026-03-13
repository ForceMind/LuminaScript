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
├─ frontend/                    前端应用
├─ deploy.sh                    Linux 一键部署脚本
├─ update.sh                    Linux 一键更新脚本
├─ uninstall.sh                 Linux 卸载脚本
├─ miaobi                       终端运维命令
├─ README.md                    使用说明
└─ API.md                       API 与管理端接口说明
```

## 本地开发
### Windows
1. 安装 Python 3.10+ 和 Node.js 18+
2. 在项目根目录运行 `start.bat`
3. 首次运行后补充 `backend/.env` 中的模型配置

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
- 启动前后端服务
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
- 用户列表查看
- 登录日志查看
- AI 审计日志查看
- 一键导出全部用户数据

### 一键导出全部用户数据
管理员面板中的“导出全部用户数据”会下载一个 ZIP，包含：
- `manifest.json`
- `users.json`
- `projects.json`
- `login_logs.json`
- `ai_logs.json`
- 数据库文件备份（如果当前环境是 SQLite）

## 运行文件说明
- `.lumina_runtime`：运行信息文件，记录当前前后端端口、日志路径、项目目录
- `backend.log`：后端运行日志
- `frontend.log`：前端运行日志
- `backups/`：更新、卸载前的自动备份目录

## 安全建议
- 尽快修改管理员默认账号密码
- 生产环境请设置强随机 `SECRET_KEY`
- 定期执行管理员数据导出并离线备份
- 对外开放端口前，确认安全组和防火墙规则