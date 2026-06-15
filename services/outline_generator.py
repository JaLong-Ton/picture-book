import json
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

OUTLINE_PROMPT = """你是一位世界级的儿童绘本设计师和故事讲述者。

## 任务
根据以下文档内容，设计一本 4-6 页的儿童绘本大纲，并提取主要角色的外观描述。

## 文档内容
{text}

{style_guidance}

## 输出要求

### 角色设定
提取故事中所有主要角色，为每个角色提供：
- **name**：角色名
- **species**：物种（如：小狐狸、小女孩、机器人）
- **appearance**：详细外观描述（用于 AI 图片生成），包括体型、毛发/肤色、眼睛、服装、配饰等。外观描述应贴合所选画风的视觉特征。

### 每页内容
为每一页提供：
1. **标题**：叙事性标题（不是"标题：副标题"格式）
2. **叙事目标**：这一页在故事中的作用
3. **关键内容**：要展示的文字（简短、适合儿童的句子）
4. **视觉画面**：场景、角色动作、表情（用于 AI 图片生成），描述应贴合所选画风
5. **布局**：构图建议

## 禁止事项
- 禁止使用"让我们一起..."、"小朋友们..."等说教语气
- 禁止"不仅仅是X，而是Y"等 AI 味道的句式
- 禁止以"谢谢观看"结尾

## 输出格式
只输出合法 JSON，不要其他文字：

{
  "title": "绘本标题",
  "characters": [
    {
      "name": "角色名",
      "species": "物种",
      "appearance": "详细外观描述，包括体型、毛发/肤色、眼睛、服装、配饰等"
    }
  ],
  "pages": [
    {
      "title": "叙事性标题",
      "narrative_goal": "这一页在故事中的作用",
      "key_content": "要展示在绘本页面上的文字",
      "visual": "场景描述，包括角色动作、表情、环境细节等",
      "layout": "构图建议"
    }
  ]
}"""

DEFAULT_STYLE_GUIDANCE = """## 画风设定
本绘本采用 **迪士尼皮克斯 3D 动画风格**。
- 角色外观应体现 3D 渲染质感：圆润柔和的线条、大而有神的眼睛、明亮温暖的色调
- 场景描述应适合 3D 动画表现：简洁的背景、柔和的光影、橙色/绿色/蓝色为主色调"""

STYLE_GUIDANCE_TEMPLATE = """## 画风设定
本绘本采用 **{style_name}**。
{style_details}
- 角色外观描述和场景描述应贴合此画风的视觉特征"""

CHARACTER_SHEET_PROMPT = """为以下儿童绘本角色创建角色设定图。

## 角色信息
名称：{name}
物种：{species}
外观：{appearance}

## 要求
1. 展示角色的 3 个角度：正面、3/4 侧面、侧面
2. 展示 3-4 种表情：开心、好奇、惊讶、思考
3. 全身和面部特写
4. 干净的白色背景
5. 图中不要出现任何文字、标签或注释——纯视觉参考

## 风格
迪士尼皮克斯 3D 动画风格
明亮温暖的色调，橙色/绿色/蓝色配色
大而有神的眼睛，圆润柔和的线条

## 布局
横版 16:9，按专业角色设定图的方式排列"""


def _call_llm(prompt: str, system: str = "你只输出合法 JSON，不输出其他文字。") -> dict:
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")
    if not api_key or not base_url or not model:
        raise RuntimeError(
            "LLM 未配置。请在 .env 中设置以下变量：\n"
            "  LLM_API_KEY=sk-xxx\n"
            "  LLM_BASE_URL=https://api.deepseek.com/v1  （或 OpenAI / 其他兼容地址）\n"
            "  LLM_MODEL=deepseek-v4-flash                （或 gpt-4o / qwen-plus 等）"
        )

    resp = requests.post(
        f"{base_url}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fences
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Extract largest JSON object brace-to-brace
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"LLM 输出不是合法 JSON: {text[:200]}...")


def generate_outline(text: str, max_pages: int = 6, template: dict | None = None) -> dict:
    if template and template.get("name"):
        style_guidance = STYLE_GUIDANCE_TEMPLATE.format(
            style_name=template["name"],
            style_details=template.get("image_prompt_style", ""),
        )
    else:
        style_guidance = DEFAULT_STYLE_GUIDANCE
    prompt = OUTLINE_PROMPT.replace("{text}", text[:8000]).replace("{style_guidance}", style_guidance)
    outline = None
    for attempt in range(3):
        content = _call_llm(prompt)
        try:
            outline = _parse_json(content)
            break
        except (ValueError, json.JSONDecodeError) as e:
            if attempt == 2:
                raise
    assert outline is not None

    # Normalize page fields
    for page in outline.get("pages", []):
        page["text"] = page.get("key_content", page.get("text", ""))
        page["illustration"] = page.get("visual", page.get("illustration", ""))

    if len(outline.get("pages", [])) > max_pages:
        outline["pages"] = outline["pages"][:max_pages]

    return outline


def build_character_sheet_prompt(character: dict, style_override: str = "") -> str:
    prompt = CHARACTER_SHEET_PROMPT
    if style_override:
        # Replace the hardcoded style block with the template's style
        old_style = """## 风格
迪士尼皮克斯 3D 动画风格
明亮温暖的色调，橙色/绿色/蓝色配色
大而有神的眼睛，圆润柔和的线条"""
        prompt = prompt.replace(old_style, f"## 风格\n{style_override}")
    return prompt.format(
        name=character.get("name", ""),
        species=character.get("species", ""),
        appearance=character.get("appearance", ""),
    )


def build_character_description(characters: list[dict]) -> str:
    """Build a concise character description block for image prompts."""
    if not characters:
        return ""
    lines = ["## 角色一致性（重要）"]
    lines.append("以下角色必须保持外观一致：")
    for c in characters:
        lines.append(f"- **{c.get('name', '未知')}**（{c.get('species', '')}）：{c.get('appearance', '')}")
    lines.append("")
    lines.append("保持一致：脸型、眼睛颜色、发型、服装。")
    lines.append("允许变化：姿势、表情、与场景的互动。")
    return "\n".join(lines)
