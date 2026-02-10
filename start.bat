@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title 妙笔流光 (LuminaScript) - 启动程序

echo ========================================================
echo        妙笔流光 (LuminaScript) 启动脚本
echo ========================================================
echo.

:: --- 1. 检查 Python ---
set PYTHON_CMD=python
python --version >nul 2>&1
if %errorlevel% equ 0 goto CHECK_NODE
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py
    goto CHECK_NODE
)
echo [Error] 未找到 Python，请安装 Python 3.10+。
pause
exit /b

:CHECK_NODE
:: --- 2. 检查 Node.js ---
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] 未找到 Node.js，请安装 Node.js 18+。
    pause
    exit /b
)

:: --- 3. 配置检查 ---
set "ENV_FILE=backend\.env"
if exist "%ENV_FILE%" goto RUN

echo [配置] 初次运行，请输入 AI 模型参数 (将自动保存到 .env):
echo.

:INPUT_KEY
set /p API_KEY="请输入 API Key: "
if "%API_KEY%"=="" goto INPUT_KEY

set /p BASE_URL="请输入 Base URL (默认 https://api.openai.com/v1): "
if "%BASE_URL%"=="" set "BASE_URL=https://api.openai.com/v1"

set /p MODEL_ID="请输入 Model ID (默认 gpt-3.5-turbo): "
if "%MODEL_ID%"=="" set "MODEL_ID=gpt-3.5-turbo"

echo.
echo 正在保存配置文件...
(
echo DATABASE_URL=sqlite+aiosqlite:///./lumina_v2.db
echo LLM_PROVIDER=openai
echo LLM_API_KEY=!API_KEY!
echo LLM_BASE_URL=!BASE_URL!
echo LLM_MODEL_ID=!MODEL_ID!
) > "%ENV_FILE%"
echo [OK] 配置已保存。

:RUN
:: --- 4. 启动后端 ---
echo [1/2] 正在启动后端...
cd backend
if not exist "venv" (
    echo [Init] 创建虚拟环境...
    %PYTHON_CMD% -m venv venv
)
call venv\Scripts\activate.bat

echo [Install] 检查依赖...
pip install -r requirements.txt >nul 2>&1

start "Lumina Backend" cmd /k "title Backend && uvicorn main:app --reload --port 8000"
cd ..

:: --- 5. 启动前端 ---
echo [2/2] 正在启动前端...
cd frontend
if not exist "node_modules" (
    echo [Init] 安装前端依赖...
    call npm install >nul 2>&1
)
start "Lumina Frontend" cmd /k "title Frontend && npm run dev"
cd ..

echo.
echo [OK] 所有服务已启动!
echo    后端 API: http://127.0.0.1:8000/docs
echo    前端页面: http://localhost:5173
echo.
pause
