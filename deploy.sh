#!/bin/bash

# ==========================================
# LuminaScript 智能部署助手 (终极版)
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

echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"

# ================= 1. 系统依赖安装 =================
echo -e "${YELLOW}[1/6] 检查并安装系统依赖...${NC}"

OS="Unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
fi
echo "当前系统: $OS"

if [[ "$OS" == *"Alibaba"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
    # RedHat 系
    if command -v dnf > /dev/null; then
        echo "使用 dnf 安装依赖..."
        sudo dnf install -y git nginx python3.11 python3.11-pip python3.11-devel bc || sudo yum install -y git python3 python3-pip nginx bc
    else
        echo "使用 yum 安装依赖..."
        sudo yum install -y git python3 python3-pip nginx bc
    fi
elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
    # Debian 系
    echo "使用 apt 安装依赖..."
    sudo apt update -qq
    sudo apt install -y python3 python3-pip python3-venv git nginx bc -qq
else
    echo -e "${YELLOW}未识别的 Linux 发行版，跳过系统依赖自动安装。请手动确保安装了 python3, git, nginx。${NC}"
fi

# ================= 2. Python 环境配置 =================
echo -e "${YELLOW}[2/6] 配置 Python 环境...${NC}"

# 寻找合适的 Python 版本 (优先 3.12 > 3.11 > 3.10)
PYTHON_EXE=""
if command -v python3.12 > /dev/null; then PYTHON_EXE="python3.12"
elif command -v python3.11 > /dev/null; then PYTHON_EXE="python3.11"
elif command -v python3.10 > /dev/null; then PYTHON_EXE="python3.10"
elif command -v python3 > /dev/null; then PYTHON_EXE="python3"
fi

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 未找到 Python 3。请手动安装。${NC}"
    exit 1
fi

# 检查版本是否满足要求 (>= 3.10)
PY_VERSION=$($PYTHON_EXE -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "选定 Python: $PYTHON_EXE (版本 $PY_VERSION)"

# 使用 Python 自身来比较版本，避免依赖 bc
IS_VALID=$($PYTHON_EXE -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)")

if [ "$IS_VALID" -eq 0 ]; then
    echo -e "${RED}[Error] LuminaScript 需要 Python 3.10+, 但当前版本为 $PY_VERSION${NC}"
    echo -e "${YELLOW}建议手动安装: sudo dnf install python3.11 (CentOS/Alibaba) 或 sudo apt install python3.11 (Ubuntu)${NC}"
    exit 1
fi

# 创建 venv
if [ ! -d "$VENV_DIR" ]; then
    echo "创建虚拟环境..."
    $PYTHON_EXE -m venv "$VENV_DIR" || {
        echo -e "${YELLOW}venv 创建失败，尝试补装 venv 模块...${NC}"
        if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
             sudo apt install -y python3-venv -qq
             $PYTHON_EXE -m venv "$VENV_DIR"
        else
             echo -e "${RED}无法创建虚拟环境，请检查 python-venv 是否安装。${NC}"
             exit 1
        fi
    }
fi

source "$VENV_DIR/bin/activate"

# ================= 3. 配置文件 (.env) =================
echo -e "${YELLOW}[3/6] 检查配置...${NC}"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}创建默认配置 .env...${NC}"
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-3.5-turbo
EOF
    echo -e "${GREEN}已创建默认 .env，请务必修改 API Key!${NC}"
fi

# ================= 4. 代码与依赖 =================
echo -e "${YELLOW}[4/6] 安装应用依赖...${NC}"

# Git 拉取 (如果存在)
if [ -d ".git" ]; then
    git pull || echo "Git pull 失败，跳过。"
fi

# pip 安装
echo "正在安装 Python 库 (这可能需要几分钟)..."
pip install --upgrade pip -q
pip install -r "$BACKEND_DIR/requirements.txt" -q || {
    echo -e "${RED}依赖安装失败。请检查 requirements.txt 或网络连接。${NC}"
    exit 1
}

# ================= 5. 前端构建 =================
echo -e "${YELLOW}[5/6] 构建前端资源...${NC}"
if command -v npm &>/dev/null; then
    cd "$FRONTEND_DIR"
    if [ ! -d "node_modules" ]; then
        npm install --silent
    fi
    npm run build
else
    echo -e "${YELLOW}未找到 npm，跳过前端构建 (请确保已上传 dist 目录)${NC}"
fi

# ================= 6. 启动服务 =================
echo -e "${YELLOW}[6/6] 启动服务 (端口自动避让)...${NC}"

# 端口检查函数
check_port() {
    local p=$1
    # 优先用 python 检测端口 (因为 python 肯定装了)
    $PYTHON_EXE -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); result = s.connect_ex(('127.0.0.1', $p)); s.close(); exit(0 if result == 0 else 1)"
    # connect_ex return 0 means success (open/listening), so it IS occupied
    # We want return 0 if occupied (consistent with typical shell logic "true it is occupied")
    return $?
}

DEFAULT_PORT=8000
PORT=$DEFAULT_PORT
MAX_RETRIES=10

for ((i=0; i<MAX_RETRIES; i++)); do
    # check_port returns 0 if occupied
    if check_port $PORT; then
        echo -e "端口 $PORT 被占用，尝试 $((PORT+1))..."
        PORT=$((PORT+1))
    else
        echo -e "${GREEN}使用端口: $PORT${NC}"
        break
    fi
done

if ((i==MAX_RETRIES)); then
    echo -e "${RED}找不到可用端口。${NC}"
    exit 1
fi

cd "$BACKEND_DIR"
# 后台启动
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT > "$PROJECT_DIR/backend.log" 2>&1 &
PID=$!

sleep 2
if ps -p $PID > /dev/null; then
    # 获取 IP
    IP=$(hostname -I | awk '{print $1}')
    PUBLIC_IP=$(curl -s --connect-timeout 2 ifconfig.me || echo "未知")
    echo -e "\n${GREEN}====== 部署成功 ======${NC}"
    echo -e "服务 PID: $PID"
    echo -e "访问地址 (内网): http://$IP:$PORT"
    echo -e "访问地址 (公网): http://$PUBLIC_IP:$PORT"
    echo -e "静态文件: $FRONTEND_DIR/dist"
    echo -e "日志监控: tail -f $PROJECT_DIR/backend.log"
else
    echo -e "${RED}启动失败，请查看 backend.log${NC}"
    exit 1
fi
