# 妙笔流光 (LuminaScript) - 自动化 AI 剧本工场

**LuminaScript** 是一个 AI 辅助剧本创作平台。它通过解决 Context Window 限制问题，实现了从“一句话灵感”到“长篇剧本”的自动化生成。

项目采用 "Human-in-the-loop"（人工策划）+ "Rolling Summary"（滚动摘要）的技术架构。

---

## 🛠️ 技术栈

*   **前端**: Vue 3, Vite, Tailwind CSS, Element Plus
*   **后端**: Python 3.10+, FastAPI, SQLAlchemy (Async)
*   **数据库**: SQLite (默认) / PostgreSQL
*   **AI**: OpenAI API / DeepSeek 兼容接口

---

## 🚀 快速开始

### 1. 本地开发 (Windows)

我们提供了一键启动脚本，自动为您准备环境并运行服务。

**前置要求**:
*   Python 3.10+
*   Node.js 18+

**启动步骤**:
1.  在项目根目录下右键打开 PowerShell。
2.  运行脚本：
    ```powershell
    .\dev.ps1
    ```
3.  脚本将自动打开两个窗口：
    *   **后端 API**: 运行在 `http://127.0.0.1:8000`
    *   **前端 UI**: 运行在 `http://localhost:5173`

### 2. 服务器部署 (Linux)

支持 Ubuntu, Debian, CentOS 等主流发行版。

**使用方法**:
1.  将项目上传至服务器。
2.  赋予脚本执行权限：
    ```bash
    chmod +x deploy.sh
    ```
3.  运行部署脚本：
    ```bash
    ./deploy.sh
    ```
    脚本会自动检测系统、安装依赖（Git, Python, Node）、拉取最新代码、构建前端并重启服务（推荐配合 PM2 使用）。

---

## 📂 项目结构

```
LuminaScript/
├── backend/            # Python FastAPI 后端
│   ├── main.py         # 入口文件 & 核心逻辑
│   ├── models.py       # 数据库模型
│   ├── schemas.py      # Pydantic 数据验证 & 交互协议
│   └── database.py     # 数据库连接
├── frontend/           # Vue 3 前端
│   ├── src/            # 页面源码
│   └── vite.config.ts  # 构建配置
├── dev.ps1             # Windows 开发启动脚本
├── deploy.sh           # Linux 部署脚本
└── README.md           # 项目文档
```

## ⚖️ License

MIT License
