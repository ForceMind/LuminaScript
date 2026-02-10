#!/bin/bash

# ==========================================
# LuminaScript 一键部署/更新脚本
# 支持 OS: Ubuntu/Debian, CentOS/RHEL/AlmaLinux
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

echo -e "${GREEN}====== 妙笔流光 (LuminaScript) 部署助手 ======${NC}"

# 1. 系统检测与依赖安装
echo -e "${YELLOW}[1/5] 检测系统环境...${NC}"

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    echo "检测到系统: $OS"
else
    echo -e "${RED}无法检测操作系统，脚本可能无法正确安装依赖。${NC}"
    exit 1
fi

install_deps() {
    if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        sudo apt-get update
        sudo apt-get install -y git python3 python3-venv python3-pip nodejs npm nginx
    elif [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]] || [[ "$OS" == *"AlmaLinux"* ]]; then
        sudo yum install -y epel-release
        sudo yum install -y git python3 python3-devel nodejs npm nginx
    else
        echo -e "${YELLOW}未适配的 Linux 发行版，跳过依赖自动安装，请确保已安装 git, python3, npm。${NC}"
    fi
}

# 询问是否安装系统依赖
read -p "是否需要安装系统基础依赖 (git, python3, nodejs)? [y/N] " install_choice
if [[ "$install_choice" =~ ^[Yy]$ ]]; then
    install_deps
fi

# 2. 拉取 Git 更新
echo -e "${YELLOW}[2/5] 拉取 Git 代码...${NC}"
if [ -d ".git" ]; then
    git pull
else
    echo -e "${RED}当前目录不是 Git 仓库，跳过 git pull。${NC}"
fi

# 3. 后端部署
echo -e "${YELLOW}[3/5] 更新后端环境...${NC}"
cd "$BACKEND_DIR"

if [ ! -d "$VENV_DIR" ]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "安装/更新 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 退出虚拟环境，但保持在 backend 目录用于后续服务重启逻辑
deactivate 

# 4. 前端构建
echo -e "${YELLOW}[4/5] 构建前端资源...${NC}"
cd "$FRONTEND_DIR"
echo "安装前端依赖..."
npm install
echo "编译生产环境代码..."
npm run build

# 5. 服务启动/重启
echo -e "${YELLOW}[5/5] 服务管理...${NC}"

# 简单的进程管理策略 (推荐使用 Systemd 或 PM2，这里提供一个简单的 PM2 示例)
if command -v pm2 &> /dev/null; then
    echo "检测到 PM2，尝试重启服务..."
    
    # 后端
    pm2 describe lumina-backend > /dev/null
    if [ $? -eq 0 ]; then
        pm2 restart lumina-backend
    else
        cd "$BACKEND_DIR"
        pm2 start "venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000" --name lumina-backend
    fi

    echo -e "${GREEN}后端服务已通过 PM2 启动/重启。${NC}"
else
    echo -e "${YELLOW}未检测到 PM2。${NC}"
    echo "建议安装 PM2 以管理服务: npm install -g pm2"
    echo "或者你可以手动启动服务："
    echo "   后端: cd backend && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000"
    echo "   前端: 已构建至 frontend/dist，请配置 Nginx 指向此目录。"
fi

echo -e "${GREEN}====== 部署完成! ======${NC}"
