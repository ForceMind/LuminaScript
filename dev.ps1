<#
.SYNOPSIS
    LuminaScript 本地开发一键启动脚本
.DESCRIPTION
    自动启动 Backend (FastAPI) 和 Frontend (Vite) 服务。
    请确保已安装 Python 3.10+ 和 Node.js 18+。
#>

Write-Host "🚀 正在启动妙笔流光 (LuminaScript) 开发环境..." -ForegroundColor Cyan

# 检查 Python 环境
Write-Host "Checking Python..." -NoNewline
try {
    $pythonVersion = python --version 2>&1
    Write-Host " OK ($pythonVersion)" -ForegroundColor Green
} catch {
    Write-Host " Failed! 请安装 Python." -ForegroundColor Red
    exit 1
}

# 检查 Node 环境
Write-Host "Checking Node.js..." -NoNewline
try {
    $nodeVersion = node --version 2>&1
    Write-Host " OK ($nodeVersion)" -ForegroundColor Green
} catch {
    Write-Host " Failed! 请安装 Node.js." -ForegroundColor Red
    exit 1
}

# 1. 启动 Backend
Write-Host "`n[1/2] 启动后端服务 (FastAPI)..." -ForegroundColor Yellow
$backendProcess = Start-Process -FilePath "powershell" -ArgumentList "-NoExit", "-Command", "& {cd backend; pip install -r requirements.txt; uvicorn main:app --reload --port 8000}" -PassThru

# 2. 启动 Frontend
Write-Host "[2/2] 启动前端服务 (Vite)..." -ForegroundColor Yellow
$frontendProcess = Start-Process -FilePath "powershell" -ArgumentList "-NoExit", "-Command", "& {cd frontend; npm install; npm run dev}" -PassThru

Write-Host "`n✅ 服务已启动!" -ForegroundColor Green
Write-Host "   后端 API: http://127.0.0.1:8000/docs"
Write-Host "   前端 UI : http://localhost:5173"
Write-Host "`n按任意键关闭所有服务..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

# 关闭进程
Stop-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue
Stop-Process -Id $frontendProcess.Id -ErrorAction SilentlyContinue
Write-Host "已关闭服务。"
