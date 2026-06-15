@echo off
chcp 65001 >nul
title AI 绘本生成器 — 一键安装
echo ============================================
echo   🎨 AI 绘本生成器 — 一键安装
echo ============================================
echo.

:: ---- check Python ----
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 未检测到 Python，请先安装 Python 3.10+
    echo    下载地址：https://www.python.org/downloads/
    echo.
    echo    安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo ✅ Python 已安装
python --version
echo.

:: ---- create .venv if not exists ----
if not exist ".venv\Scripts\activate.bat" (
    echo 📦 正在创建虚拟环境...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo ❌ 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo ✅ 虚拟环境已创建
) else (
    echo ✅ 虚拟环境已存在
)
echo.

:: ---- activate venv ----
call .venv\Scripts\activate.bat

:: ---- upgrade pip ----
python -m pip install --upgrade pip -q

:: ---- install deps ----
echo 📦 正在安装依赖...
pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 安装依赖失败
    pause
    exit /b 1
)
echo ✅ 依赖安装完成
echo.

:: ---- .env ----
if not exist ".env" (
    set NEW_ENV=1
    echo 📝 正在创建 .env 配置文件...
    copy .env.example .env >nul
    echo.
    echo ⚠️  首次使用需要配置 API 密钥！
    echo    即将打开记事本编辑 .env 文件
    echo.
    echo    请按照 .env 里的注释说明填写密钥。
    echo.
    pause
    start /wait notepad .env
    echo.
) else (
    set NEW_ENV=0
    echo ✅ .env 配置文件已存在
)
echo.

:: ---- done ----
echo ============================================
echo   🚀 启动应用...
echo.
echo   访问地址：http://127.0.0.1:5000
echo ============================================
echo.

python app.py
pause
