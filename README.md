<div align="center">

# 🎨 AI 绘本生成器

**上传文档 → AI 自动生成带插图的儿童绘本 PDF**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=fff)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0-000?logo=flask)](https://flask.palletsprojects.com/)
[![MIT License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![OpenAI Compatible](https://img.shields.io/badge/LLM-OpenAI%20Compatible-4F5B66)](https://api.deepseek.com)
[![Reports](https://img.shields.io/badge/PDF-ReportLab-FF6B6B)](https://www.reportlab.com/)

</div>

---

## ✨ 它能做什么

上传一份文档（PDF / Word / TXT），AI 自动把它变成一本带插图的儿童绘本 PDF：

```
选择画风模板 → 上传文档 → AI 解析 → AI 生成大纲 → 预览 & 编辑 → AI 绘制插图 → 合成 PDF
```

支持 **6 种画风**：迪士尼3D、水彩手绘、中国水墨、日系动漫、剪纸风、蜡笔童趣

> ⚡ **完全本地运行**，你的 API Key 只在你自己的电脑上使用。

---

## 🚀 快速开始

### 前置要求

- **Python 3.10+**（[下载](https://www.python.org/downloads/)）
- 两个 API 密钥（下文有详细说明）

### 一键启动（推荐）

**Windows：**
```bash
双击 setup.bat
```
或
```bash
.\setup.bat
```

**macOS / Linux：**
```bash
chmod +x setup.sh && ./setup.sh
```

脚本会自动完成：创建虚拟环境 → 安装依赖 → 生成配置文件 → 启动应用。

### 手动安装

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/ai-picture-book.git
cd ai-picture-book

# 2. 创建虚拟环境
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API 密钥
cp .env.example .env
# 编辑 .env，填入你的密钥

# 5. 启动
python app.py
```

打开浏览器访问 **http://127.0.0.1:5000** 🎉

---

## 🔑 API 密钥说明

你需要两个 API 密钥（全部在 `.env` 中配置）：**图片生成密钥** 和 **LLM 密钥**。

### 🖼 图片生成 — 支持的服务商

| 服务商 | 配置 | 
|--------|------|
| **gpt-image-2** (默认) | `IMAGE_PROVIDER=nano-banana`, API Key 从 [grsaiapi.com](https://grsaiapi.com) 获取 |
| **DALL-E 3** | `IMAGE_PROVIDER=openai-dalle`, 使用你的 OpenAI API Key |

配置示例（以默认服务商为例）：

```ini
IMAGE_PROVIDER=nano-banana
IMAGE_API_KEY=sk-your-key-here       # 在 grsaiapi.com 注册获取
IMAGE_API_BASE=https://grsaiapi.com
IMAGE_MODEL=gpt-image-2
```

切换到 DALL-E 3：

```ini
IMAGE_PROVIDER=openai-dalle
IMAGE_API_KEY=sk-xxx                 # 你的 OpenAI API Key
IMAGE_API_BASE=https://api.openai.com/v1
IMAGE_MODEL=dall-e-3
```

### 💬 故事大纲 — 支持任何 OpenAI 兼容接口

主流选择：

| 服务商 | `LLM_BASE_URL` | `LLM_MODEL` 示例 |
|--------|----------------|------------------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-v4-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.1-70b-versatile` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

配置示例：

```ini
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-llm-api-key
LLM_MODEL=deepseek-v4-flash
```

> 💡 只要兼容 OpenAI Chat Completions 接口就能用，不受以上列表限制。

### MinerU（可选，仅扫描版 PDF 需要）

如果上传的 PDF 是扫描件（图片格式），需要 OCR 识别。在 [mineru.net](https://mineru.net) 申请 API Token 并填入 `.env`：

```ini
MINERU_API_KEY=your-mineru-token
```

普通文本 PDF 不需要此配置。

---

## 🎨 模板市场

内置 6 种画风模板，在首页选择后一键切换绘本风格：

| 模板 | 风格 | 适合 |
|------|------|------|
| 🏰 迪士尼3D | 明亮温暖的 3D 动画风格 | 大多数绘本故事 |
| 🖌 水彩手绘 | 柔和晕染的水彩质感 | 梦幻温馨的故事 |
| 🏮 中国水墨 | 传统水墨画风，留白意境 | 古典故事 |
| 🌸 日系动漫 | 精致细腻的动漫插画 | 少女/冒险故事 |
| ✂️ 剪纸风 | 中国传统剪纸艺术 | 民间故事 |
| 🖍 蜡笔童趣 | 粗犷质感的蜡笔画 | 低龄儿童绘本 |

模板贯穿全流程：影响大纲生成（角色外观描述）→ 角色设定图 → 页面插图，三个阶段的 prompt 都会体现所选画风。

---

## 🗺️ 工作流程详情

1. **选择画风** → 从模板市场选择喜欢的画风（可跳过，默认迪士尼3D）
2. **上传文档** → 支持 PDF、Word (.doc/.docx)、TXT（可选上传人物照片）
3. **AI 解析文档** → 自动提取文本内容
4. **AI 生成大纲** → 生成绘本标题、角色设定、每页图文内容
5. **预览 & 编辑** → 可修改标题和各页文字，满意后再生成
6. **AI 绘制插图** → 按所选画风为每页生成插图（支持断点续传）
7. **合成 PDF 下载** → 自动生成带全屏插图和文字的绘本 PDF

---

## 📁 项目结构

```
├── app.py                          # Flask 主应用（路由、任务管理）
├── services/
│   ├── document_parser.py          # 文档解析（PDF/Word/TXT）
│   ├── mineru_service.py           # MinerU 云端 OCR（可选）
│   ├── outline_generator.py        # LLM 大纲生成
│   ├── image_service.py            # 图片生成（自动选择后端）
│   ├── image_provider.py           # 多后端策略层（NanoBanana / DALL-E 3）
│   └── pdf_exporter.py             # PDF 合成（支持中文）
├── templates/
│   └── index.html                  # 前端页面（Tailwind CSS）
├── setup.bat                       # Windows 一键安装启动
├── setup.sh                        # macOS/Linux 一键安装启动
├── Dockerfile                      # Docker 部署
├── .env.example                    # 环境变量模板
└── requirements.txt                # Python 依赖
```

运行时会自动生成：
- `uploads/` — 上传的文档
- `outputs/` — 生成的绘本（图片 + PDF）
- `data/jobs.db` — 任务数据库

---

## 🐳 Docker 部署

```bash
# 构建镜像
docker build -t ai-picture-book .

# 编辑 .env 填入密钥后运行
docker run -p 5000:5000 \
  -v "$(pwd)/.env:/app/.env" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/outputs:/app/outputs" \
  -v "$(pwd)/uploads:/app/uploads" \
  ai-picture-book
```

---

## ⚙️ 特性

- **模板市场** — 6 种画风一键切换，模板贯穿全链路
- **大纲预览** — 生成前可编辑绘本内容
- **断点续传** — 已生成的插图会缓存，中断后继续
- **任务持久化** — SQLite 保存状态，重启不丢失
- **自动清理** — 过期任务自动清除（默认 24 小时）
- **人物照片** — 可选上传照片，AI 将人物融入插图
- **中文支持** — PDF 完整支持中文渲染
- **SSE 实时进度** — 前端实时显示生成进度
- **模板预览图**（可选）— 运行 `python generate_template_previews.py` 生成真实的画风预览图（默认显示渐变色占位图）

---

## 🐛 常见问题

**Q：启动报错 "No module named ..."**
```
确保已激活虚拟环境，然后重新安装依赖：
source .venv/bin/activate
pip install -r requirements.txt
```

**Q：生成图片很慢或者失败**
```
1. 检查 IMAGE_API_KEY 是否正确配置
2. 检查 .env 中 IMAGE_API_BASE 和 IMAGE_PROVIDER 是否匹配你的服务商
3. 查看终端日志中的具体错误信息
```

**Q：中文显示为方框**
```
系统缺少中文字体。
Windows / macOS 通常自带，Linux 需要安装：
sudo apt install fonts-wqy-microhei
```

**Q：上传的 PDF 是扫描件，无法提取文字**
```
需要配置 MinerU API Key（见上方说明），
或者先通过 OCR 软件将 PDF 转为文本再上传。
```

---

## 📄 许可证

[MIT](LICENSE)
