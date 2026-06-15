#!/usr/bin/env bash
set -e

echo "============================================"
echo "  🎨 AI 绘本生成器 — 一键安装"
echo "============================================"
echo ""

# ---- check Python ----
if ! command -v python3 &>/dev/null; then
    echo "❌ 未检测到 Python 3，请先安装 Python 3.10+"
    echo "   下载地址：https://www.python.org/downloads/"
    exit 1
fi
echo "✅ Python 已安装：$(python3 --version)"

# ---- create .venv if not exists ----
if [ ! -d ".venv" ]; then
    echo "📦 正在创建虚拟环境..."
    python3 -m venv .venv
    echo "✅ 虚拟环境已创建"
else
    echo "✅ 虚拟环境已存在"
fi
echo ""

# ---- activate venv ----
source .venv/bin/activate

# ---- upgrade pip ----
python3 -m pip install --upgrade pip -q

# ---- install deps ----
echo "📦 正在安装依赖..."
pip install -r requirements.txt -q
echo "✅ 依赖安装完成"
echo ""

# ---- .env ----
if [ ! -f ".env" ]; then
    echo "📝 正在创建 .env 配置文件..."
    cp .env.example .env
    echo ""
    echo "⚠️  首次使用需要配置 API 密钥！"
    echo "   请编辑 .env 文件，按注释说明填入密钥："
    echo "       nano .env"
    echo ""
    echo "   编辑保存后按回车继续启动..."
    read -r
else
    echo "✅ .env 配置文件已存在"
fi
echo ""

# ---- done ----
echo "============================================"
echo "  🚀 启动应用..."
echo ""
echo "  访问地址：http://127.0.0.1:5000"
echo "============================================"
echo ""

python app.py
