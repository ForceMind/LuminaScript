#!/bin/bash

# ==========================================
# LuminaScript 一键部署脚本 (修复版 v2)
# ==========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_DIR=$(pwd)
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"

echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 v2 ======${NC}"

# ================= 0. 环境预检 =================

echo -e "${YELLOW}[0/5] 环境预检...${NC}"

# 检查 Python 版本
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "识别到 Python 版本: $PY_VER"
    
    # 简单的版本比较逻辑 (检查是否小于 3.8)
    # 若 bc 不存在，只能粗略判断
    if (( $(echo "$PY_VER < 3.8" | bc -l 2>/dev/null || echo 0) )); then
        echo -e "${RED}错误: Python 版本过低 ($PY_VER)。LuminaScript 依赖库需要 Python 3.8+。${NC}"
        echo "当前系统可能默认 Python 版本较旧。"
        echo "建议: 安装 Python 3.9或更高版本，或使用 Docker 部署。"
        exit 1
    fi
else
    echo -e "${RED}错误: 未找到 python3。请安装 Python 3.8+。${NC}"
    exit 1
fi

# 检查 npm
HAS_NPM=0
if command -v npm &>/dev/null; then
    HAS_NPM=1
    echo "识别到 npm: $(npm -v)"
else
    echo -e "${YELLOW}警告: 未找到 npm。前端构建步骤将被跳过。${NC}"
    echo "请确保您已手动上传了编译好的 frontend/dist 目录。"
fi

# ================= 1. API 配置 =================
echo -e "${YELLOW}[1/5] 检查配置...${NC}"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}未检测到 .env，正在创建默认配置...${NC}"
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-3.5-turbo
EOF
    echo -e "${GREEN}默认 .env 已创建。请务必编辑 $ENV_FILE 填入真实 Key。${NC}"
else
    echo "检测到现有配置。"
fi

# ================= 2. 代码更新 =================
echo -e "${YELLOW}[2/5] 拉取代码...${NC}"
if [ -d ".git" ]; then
    git pull || echo -e "${RED}Git 拉取失败，将使用现有代码继续...${NC}"
fi

# ================= 2.5 端口清理 =================
PORT=8000
echo -e "${YELLOW}[2.5/5] 检查端口 $PORT...${NC}"

# 兼容多种检查方式
PID=""
if command -v lsof &>/dev/null; then
    PID=$(lsof -t -i:$PORT)
elif command -v netstat &>/dev/null; then
    PID=$(netstat -nlp | grep :$PORT | awk '{print $7}' | cut -d'/' -f1)
elif command -v fuser &>/dev/null; then
    PID=$(fuser $PORT/tcp 2>/dev/null)
fi

if [ ! -z "$PID" ]; then
    echo -e "${YELLOW}端口 $PORT 被进程 $PID 占用。正在尝试关闭...${NC}"
    kill -9 $PID 2>/dev/null || true
    sleep 2
fi

# ================= 3. 后端部署 =================
echo -e "${YELLOW}[3/5] 部署后端...${NC}"
cd "$BACKEND_DIR"

# 创建或激活 venv
if [ ! -d "$VENV_DIR" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate

# 尝试安装依赖
echo "安装/更新依赖..."
pip install --upgrade pip
# 尝试安装，若失败则提示
if ! pip install -r requirements.txt; then
    echo -e "${RED}依赖安装遇到问题！${NC}"
    echo "正在尝试放宽依赖版本限制..."
    # 尝试单独安装旧版 fastapi 兼容旧 python (虽然不推荐)
    pip install "fastapi<0.100.0" "pydantic<2.0.0" "typing_extensions" --no-deps
    pip install -r requirements.txt
fi

# ================= 4. 前端构建 =================
echo -e "${YELLOW}[4/5] 构建前端...${NC}"
if [ $HAS_NPM -eq 1 ]; then
    cd "$FRONTEND_DIR"
    if [ ! -d "node_modules" ]; then
        echo "安装前端依赖 (npm install)..."
        npm install
    fi
    echo "编译前端 (npm run build)..."
    npm run build
else
    echo "跳过前端构建 (无 npm)。"
fi

# ================= 5. 启动服务 =================
echo -e "${YELLOW}[5/5] 启动服务...${NC}"
cd "$BACKEND_DIR"
# 后台启动
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 > "$PROJECT_DIR/backend.log" 2>&1 &
SERVER_PID=$!

sleep 3
if ps -p $SERVER_PID > /dev/null; then
    echo -e "${GREEN}后端服务已启动! PID: $SERVER_PID${NC}"
else
    echo -e "${RED}后端服务启动似乎失败了。请查看日志:${NC}"
    tail -n 10 "$PROJECT_DIR/backend.log"
    echo -e "${RED}提示: 如果是 ImportError，通常是 Python 版本过低导致。${NC}"
    exit 1
fi

# ================= 6. 完成信息 =================
# 获取 IP 地址
IP=$(hostname -I | awk '{print $1}')
PUBLIC_IP=$(curl -s --connect-timeout 2 ifconfig.me || echo "未知")

echo -e "\n${GREEN}====== 部署成功 ======${NC}"
echo -e "访问地址 (内网):  http://$IP:8000"
echo -e "访问地址 (公网):  http://$PUBLIC_IP:8000"
echo -e "前端构建目录:     $FRONTEND_DIR/dist"
echo -e "------------------------------------------------"
echo -e "注意: 若无法访问，请检查服务器防火墙 (安全组) 是否放行 8000 端口。"
echo -e "日志监控: tail -f $PROJECT_DIR/backend.log"
