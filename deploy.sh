#!/bin/bash

# ==========================================
# LuminaScript 一键部署/更新脚本
# ==========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROJECT_DIR=$(pwd)
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"

echo -e "${GREEN}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"

# 1. API 配置检查
echo -e "${YELLOW}[1/5] 检查环境配置...${NC}"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}未检测到 .env 配置文件，开始配置...${NC}"
    echo "请输入您的 AI 模型配置 (不做验证，直接写入 .env):"
    
    read -p "请输入 API Key: " API_KEY
    read -p "请输入 Base URL (默认: https://api.openai.com/v1): " BASE_URL
    BASE_URL=${BASE_URL:-https://api.openai.com/v1}
    read -p "请输入 Model ID (默认: gpt-3.5-turbo): " MODEL_ID
    MODEL_ID=${MODEL_ID:-gpt-3.5-turbo}

    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=$API_KEY
LLM_BASE_URL=$BASE_URL
LLM_MODEL_ID=$MODEL_ID
EOF
    echo -e "${GREEN}配置已保存至 $ENV_FILE${NC}"
else
    echo "检测到现有配置，跳过。"
fi

# 2. 拉取 Git 更新
echo -e "${YELLOW}[2/5] 拉取 Git 代码...${NC}"
if [ -d ".git" ]; then
    git pull
else
    echo "非 Git 仓库，跳过拉取。"
fi

# 检查端口占用 (默认 8000)
PORT=8000
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null ; then
        return 0
    else
        return 1
    fi
}

echo -e "${YELLOW}[2.5/5] 检查端口占用...${NC}"
if check_port $PORT; then
    echo -e "${RED}警告: 端口 $PORT 已被占用。${NC}"
    echo "尝试查找并终止占用该端口的进程..."
    # 仅作演示，危险操作建议手动确认
    # kill -9 $(lsof -t -i:$PORT)
    read -p "是否尝试终止占用端口的进程? (y/n) " KILL_PROC
    if [ "$KILL_PROC" == "y" ]; then
         fuser -k $PORT/tcp
         echo "已尝试终止进程。"
    else
         echo "请手动更改端口或终止进程后重试。"
         exit 1
    fi
fi


# 3. 后端部署
echo -e "${YELLOW}[3/5] 更新后端环境...${NC}"
cd "$BACKEND_DIR"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 停止旧进程 (简单的 kill 方式，生产环境建议更稳健的方式)
echo "正在检查旧的后端进程..."
pkill -f "uvicorn main:app" || true

# 4. 前端构建
echo -e "${YELLOW}[4/5] 构建前端资源...${NC}"
cd "$FRONTEND_DIR"
npm install
npm run build
echo -e "${GREEN}前端构建完成。请确保 Nginx 指向: $FRONTEND_DIR/dist${NC}"

# 5. 启动后端 (nohup 后台运行)
echo -e "${YELLOW}[5/5] 启动后端服务...${NC}"
cd "$BACKEND_DIR"
# 确保在 venv 下运行
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 > "$PROJECT_DIR/backend.log" 2>&1 &

echo -e "${GREEN}====== 部署完成! ======${NC}"
echo -e "后端日志查看: tail -f $PROJECT_DIR/backend.log"
