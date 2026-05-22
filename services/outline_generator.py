import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

OUTLINE_PROMPT = """你是一位世界级的儿童绘本设计师和故事讲述者。

## 任务
根据以下文档内容，设计一本 10-15 页的儿童绘本大纲，并提取主要角色的外观描述。

## 文档内容
{text}

## 输出要求

### 角色设定
提取故事中所有主要角色，为每个角色提供：
- **name**：角色名
- **species**：物种（如：小狐狸、小女孩、机器人）
- **appearance**：详细外观描述（英文，用于 AI 图片生成），包括体型、毛发/肤色、眼睛、服装、配饰等

### 每页内容
为每一页提供：
1. **标题**：叙事性标题（不是"标题：副标题"格式）
2. **叙事目标**：这一页在故事中的作用
3. **关键内容**：要展示的文字（简短、适合儿童的句子）
4. **视觉画面**：场景、角色动作、表情（英文，用于 AI 图片生成）
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
      "appearance": "Detailed appearance description in English for AI image generation..."
    }
  ],
  "pages": [
    {
      "title": "叙事性标题",
      "narrative_goal": "这一页在故事中的作用",
      "key_content": "要展示在绘本页面上的文字",
      "visual": "Detailed scene description in English including character actions and expressions...",
      "layout": "构图建议"
    }
  ]
}"""

CHARACTER_SHEET_PROMPT = """Create a character reference sheet for a children's picture book character.

## Character Info
Name: {name}
Species: {species}
Appearance: {appearance}

## Requirements
1. Show the character from 3 angles: front, 3/4 view, side
2. Show 3-4 expressions: happy, curious, surprised, thinking
3. Full body and close-up face views
4. Clean white background
5. No text, labels, or annotations in the image — pure visual reference only

## Style
Disney Pixar 3D animation style
Bright warm colors, orange/green/blue palette
Big expressive eyes, rounded soft lines

## Layout
Landscape 16:9, arranged like a professional character design sheet"""


def _call_llm(prompt: str, system: str = "你只输出合法 JSON，不输出其他文字。") -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("NANO_BANANA_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("NANO_BANANA_API_BASE", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o")

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
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def generate_outline(text: str, max_pages: int = 15) -> dict:
    prompt = OUTLINE_PROMPT.replace("{text}", text[:8000])
    content = _call_llm(prompt)
    outline = _parse_json(content)

    # Normalize page fields
    for page in outline.get("pages", []):
        page["text"] = page.get("key_content", page.get("text", ""))
        page["illustration"] = page.get("visual", page.get("illustration", ""))

    if len(outline.get("pages", [])) > max_pages:
        outline["pages"] = outline["pages"][:max_pages]

    return outline


def build_character_sheet_prompt(character: dict) -> str:
    return CHARACTER_SHEET_PROMPT.format(
        name=character.get("name", ""),
        species=character.get("species", ""),
        appearance=character.get("appearance", ""),
    )


def build_character_description(characters: list[dict]) -> str:
    """Build a concise character description block for image prompts."""
    if not characters:
        return ""
    lines = ["## Character Consistency (IMPORTANT)"]
    lines.append("The following characters MUST appear consistent with these descriptions:")
    for c in characters:
        lines.append(f"- **{c.get('name', 'Unknown')}** ({c.get('species', '')}): {c.get('appearance', '')}")
    lines.append("")
    lines.append("Maintain identical: face shape, eye color, hair style, clothing.")
    lines.append("Allowed to change: pose, expression, interaction with scene.")
    return "\n".join(lines)
