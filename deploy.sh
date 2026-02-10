#!/bin/bash

# ==========================================
# LuminaScript 智能部署助手 (稳定版)
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
# 解决低配服务器运行 dnf/yum/pip 时的 "Killed" 问题
check_swap() {
    SWAP_SIZE=$(free -m | grep Swap | awk '{print $2}')
    if [ "$SWAP_SIZE" -eq 0 ]; then
        echo -e "${YELLOW}[0/6] 检测到无 Swap，正在创建 2GB 临时 Swap 以防 OOM...${NC}"
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        echo "/swapfile none swap sw 0 0" >> /etc/fstab
        echo -e "${GREEN}Swap 创建成功!${NC}"
    else
        echo "检测到 Swap: ${SWAP_SIZE}MB (跳过创建)"
    fi
}
# 尝试创建 swap，需要 sudo 权限
if [ "$EUID" -eq 0 ]; then
    check_swap
else
    echo -e "${YELLOW}非 root 用户运行，跳过 Swap 自动创建。若后续安装失败，请手动增加 Swap。${NC}"
fi

# ================= 1. 系统依赖安装 =================
echo -e "${YELLOW}[1/6] 检查并安装系统依赖...${NC}"

OS="Unknown"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
fi
echo "当前系统: $OS"

install_deps_if_missing() {
    # 优先尝试安装 Python 3.11，如果源里没有则回退
    if [[ "$OS" == *"Alibaba"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
        echo "正在更新包管理器缓存..."
        # 尝试安装 EPEL 源（如果不存在）
        yum install -y epel-release 2>/dev/null

        if command -v dnf > /dev/null; then
            # 增加 --memory-limit 尝试防止 OOM，但最有效的是上面的 swap
            sudo dnf install -y git nginx python3.11 python3.11-pip python3.11-devel bc || \
            sudo dnf install -y git nginx python3 python3-pip python3-devel bc
        else
            sudo yum install -y git nginx python3 python3-pip python3-devel bc
        fi
    elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        sudo apt update -qq
        # Ubuntu 20.04+ 通常有 python3.8+，尝试添加 deadsnakes PPA 以防万一 (暂略，直接用默认)
        sudo apt install -y python3 python3-pip python3-venv git nginx bc -qq
    fi
}

install_deps_if_missing

# ================= 2. Python 环境配置 =================
echo -e "${YELLOW}[2/6] 配置 Python 环境...${NC}"

# 寻找合适的 Python 版本
# 注意：RedHat 系可能叫 python3.11，Debian 系通常叫 python3
PYTHON_EXE=""
for callback in python3.12 python3.11 python3.10 python3; do
    if command -v $callback > /dev/null; then
        # 检查具体版本号
        VER=$($callback -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        # 简单浮点比较
        IS_OK=$(echo "$VER >= 3.10" | bc -l)
        if [ "$IS_OK" -eq 1 ]; then
            PYTHON_EXE=$callback
            echo "选定 Python: $PYTHON_EXE (版本 $VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo -e "${RED}[Error] 未找到 Python 3.10+。LuminaScript 依赖新版 Python 特性。${NC}"
    echo "依赖安装步骤可能因内存不足被系统 Kill，导致新版 Python 未能安装成功。"
    echo "建议重试，或手动安装 python 3.11: sudo dnf install python3.11"
    exit 1
fi

# 创建 venv
# 无论如何，重新创建 venv 以确保 clean
if [ -d "$VENV_DIR" ]; then
    echo "清理旧的虚拟环境..."
    rm -rf "$VENV_DIR"
fi

echo "创建虚拟环境 ($VENV_DIR)..."
$PYTHON_EXE -m venv "$VENV_DIR" || {
    echo -e "${RED}虚拟环境创建失败!${NC}"
    echo "可能原因: 缺少 python3-venv 包 (Debian/Ubuntu) 或 内存不足。"
    exit 1
}

# 强制使用 venv 中的 pip
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ================= 3. 配置文件 (.env) =================
# ... (保持不变)
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
LLM_PROVIDER=openai
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-3.5-turbo
EOF
fi

# ================= 4. 代码与依赖 =================
echo -e "${YELLOW}[4/6] 安装应用依赖...${NC}"

if [ -d ".git" ]; then
    git pull || echo "Git pull 失败，跳过。"
fi

echo "正在安装 Python 库..."
# 使用阿里云镜像加速，减少超时概率
$VENV_PIP install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
$VENV_PIP install -r "$BACKEND_DIR/requirements.txt" -i https://mirrors.aliyun.com/pypi/simple/ || {
    echo -e "${RED}依赖安装失败!${NC}"
    echo "如果是 'killed'，请确保 Swap 已启用。"
    echo "如果是 'No matching distribution'，请确认 python 版本 >= 3.10。"
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
    echo -e "${YELLOW}跳过前端构建 (无 npm)${NC}"
fi

# ================= 6. 启动服务 =================
echo -e "${YELLOW}[6/6] 启动服务...${NC}"

# 端口检查 (Python 实现)
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
        echo "端口 $PORT 被占用，尝试下一端口..."
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
