#!/bin/bash

# ==========================================
# LuminaScript 智能部署助手 (全栈版)
# ==========================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR=$(pwd)
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
ENV_FILE="$BACKEND_DIR/.env"

echo -e "${BLUE}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"

# ================= 0. 内存优化 (自动 SWAP) =================
# 解决低配服务器运行 dnf/yum/pip/npm 时的 "Killed" 问题
check_swap() {
    SWAP_SIZE=$(free -m | grep Swap | awk '{print $2}')
    if [ "$SWAP_SIZE" -eq 0 ]; then
        echo -e "${YELLOW}[0/6] 检测到无 Swap，正在创建 2GB 临时 Swap 以防 OOM...${NC}"
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        if ! grep -q "/swapfile" /etc/fstab; then
            echo "/swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}Swap 创建成功!${NC}"
    else
        echo "检测到 Swap: ${SWAP_SIZE}MB (跳过创建)"
    fi
}
if [ "$EUID" -eq 0 ]; then
    check_swap
else
    echo -e "${YELLOW}非 root 用户运行，跳过 Swap 自动创建。${NC}"
fi

# ================= 1. 系统依赖安装 (含 Node.js) =================
echo -e "${YELLOW}[1/6] 检查并安装系统依赖 (Python & Node.js)...${NC}"

OS="Unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
fi
echo "当前系统: $OS"

install_系统软件() {
    if [[ "$OS" == *"Alibaba"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
        # 安装 Node.js 18.x 源
        if ! command -v node > /dev/null; then
            echo "添加 Node.js 源..."
            curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -
        fi
        
        sudo yum install -y epel-release 2>/dev/null
        # 尝试 dnf 或 yum
        PKG_MGR="yum"
        if command -v dnf > /dev/null; then PKG_MGR="dnf"; fi
        
        sudo $PKG_MGR install -y git nginx python3.11 python3.11-pip python3.11-devel bc nodejs
    elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        # 安装 Node.js 18.x 源
        if ! command -v node > /dev/null; then
             curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
        fi
        sudo apt update -qq
        sudo apt install -y python3 python3-pip python3-venv git nginx bc nodejs -qq
    fi
}

install_系统软件

# 验证 Node.js
if command -v node > /dev/null && command -v npm > /dev/null; then
    NODE_VER=$(node -v)
    echo -e "${GREEN}Node.js 已就绪: $NODE_VER${NC}"
else
    echo -e "${RED}Node.js 安装失败，前端无法构建。${NC}"
    exit 1
fi

# ================= 2. Python 环境配置 =================
echo -e "${YELLOW}[2/6] 配置 Python 环境...${NC}"

PYTHON_EXE=""
for callback in python3.12 python3.11 python3.10 python3; do
    if command -v $callback > /dev/null; then
        VER=$($callback -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        IS_OK=$(echo "$VER >= 3.10" | bc -l)
        if [ "$IS_OK" -eq 1 ]; then
            PYTHON_EXE=$callback
            echo "选定 Python: $PYTHON_EXE (版本 $VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 未找到 Python 3.10+。${NC}"
    exit 1
fi

# 重建 venv
if [ -d "$VENV_DIR" ]; then rm -rf "$VENV_DIR"; fi
echo "创建虚拟环境 ($VENV_DIR)..."
$PYTHON_EXE -m venv "$VENV_DIR"

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ================= 3. 配置文件 (.env) =================
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-3.5-turbo
EOF
fi

# ================= 4. 后端依赖 =================
echo -e "${YELLOW}[4/6] 安装后端依赖...${NC}"
if [ -d ".git" ]; then git pull; fi
echo "正在安装 Python 库..."
$VENV_PIP install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
$VENV_PIP install -r "$BACKEND_DIR/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/

# ================= 5. 前端构建 =================
echo -e "${YELLOW}[5/6] 构建前端资源...${NC}"
cd "$FRONTEND_DIR"

# 设置 npm 淘宝镜像加速
npm config set registry https://registry.npmmirror.com

echo "安装前端依赖..."
if [ ! -d "node_modules" ]; then
    npm install
else
    # 简单的全部重装耗时太久，尝试直接 install
    npm install
fi

echo "编译前端应用..."
# 尝试消除 vue-tsc 版本不兼容问题: 如果构建失败，尝试仅使用 vite build
if ! npm run build; then
    echo -e "${YELLOW}标准构建失败 (可能是 vue-tsc 类型检查问题)，尝试跳过类型检查强制构建...${NC}"
    # 临时使用 vite build
    ./node_modules/.bin/vite build || {
        echo -e "${RED}前端构建再次失败!${NC}"
        echo "提示: 如果出现 'Killed' 错误，请检查 Swap 是否已成功挂载。"
        exit 1
    }
fi

# ================= 6. 启动服务 =================
echo -e "${YELLOW}[6/6] 启动服务...${NC}"

# Python 端口检查
check_port() {
    $VENV_PYTHON -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); exit(0 if s.connect_ex(('127.0.0.1', $1)) != 0 else 1)"
    return $?
}

DEFAULT_PORT=8000
PORT=$DEFAULT_PORT

for ((i=0; i<10; i++)); do
    if check_port $PORT; then
        echo -e "${GREEN}使用端口: $PORT${NC}"
        break
    else
        echo "端口 $PORT 被占用，尝试 $((PORT+1))..."
        PORT=$((PORT+1))
    fi
done

cd "$BACKEND_DIR"
nohup "$VENV_DIR/bin/uvicorn" main:app --host 0.0.0.0 --port $PORT > "$PROJECT_DIR/backend.log" 2>&1 &
PID=$!

sleep 2
if ps -p $PID > /dev/null; then
    IP=$(hostname -I | awk '{print $1}')
    echo -e "\n${GREEN}====== 部署成功 ======${NC}"
    echo -e "访问地址: http://$IP:$PORT"
    echo -e "日志监控: tail -f $PROJECT_DIR/backend.log"
else
    echo -e "${RED}启动失败，请查看 backend.log${NC}"
    exit 1
fi
