"""GET /api/agents：智能体定义加载。

优先读 .opencode/agents/*.md（opencode 格式：无 name/tools/skills 字段，
permission 为嵌套映射）；该目录不存在时回退 .claude/agents/*.md（Claude 格式：
name/skills/tools/color）。目录存在但为空不回退；单文件读取/解析失败记入
load_error，不伪装成功、不静默改用另一格式。固定流程角色来自
stage_inference.AGENTS_FLOW，未找到定义文件的角色 definition_found=False。
"""
import re

from ..parsers import frontmatter as fm
from .stage_inference import AGENTS_FLOW

# opencode 正文中的技能引用句式：「立即用 skill 工具加载 `xxx` 技能」
_SKILL_RE = re.compile(r"加载\s*[`'\"]([\w-]+)[`'\"]\s*技能")


def _skills_from_meta(meta):
    # Claude 格式：skills 列表或逗号串
    skills = meta.get("skills")
    if isinstance(skills, list):
        return [str(s) for s in skills]
    if isinstance(skills, str) and skills.strip():
        return [part.strip() for part in skills.split(",") if part.strip()]
    return []


def _skills_from_body(text):
    # opencode 格式：从正文句式提取
    return list(dict.fromkeys(_SKILL_RE.findall(text)))


def _body_of(text):
    # 去掉 frontmatter，只留正文
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1:])
        return "\n".join(lines[1:])
    return text


def _load_from_dir(dir_path, source):
    """读一个定义目录。目录不存在返回 None；存在则返回 {name: entry}。"""
    if not dir_path.is_dir():
        return None
    defs = {}
    for md in sorted(dir_path.glob("*.md")):
        stem = md.stem
        entry = {
            "name": stem,
            "description": "",
            "mode": "",
            "color": "blue",
            "skills": [],
            "permission": None,
            "tools": "",
            "definition_found": True,
            "load_error": None,
            "source": source,
            "file": "%s/%s" % (source, md.name),
        }
        try:
            text = md.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            entry["load_error"] = "读取失败: %s" % exc
            defs[stem] = entry
            continue
        meta = fm.parse_frontmatter(text)
        structural = meta.pop("_error", None)
        # 读取层已保证 UTF-8；此处 load_error 只记结构错误
        if structural:
            entry["load_error"] = structural
        name = meta.get("name")
        if isinstance(name, str) and name.strip():
            entry["name"] = name.strip()
            entry["file"] = "%s/%s" % (source, md.name)
        if isinstance(meta.get("description"), str):
            entry["description"] = meta["description"]
        if isinstance(meta.get("mode"), str):
            entry["mode"] = meta["mode"]
        if isinstance(meta.get("color"), str):
            entry["color"] = meta["color"].strip().strip("'\"") or "blue"
        permission = meta.get("permission")
        if isinstance(permission, dict):
            entry["permission"] = permission
        if isinstance(meta.get("tools"), str):
            entry["tools"] = meta["tools"]
        source_skills = _skills_from_meta(meta) or _skills_from_body(_body_of(text))
        entry["skills"] = source_skills
        defs[entry["name"]] = entry
    return defs


def load_agent_defs(root):
    # type: (...) -> list
    """返回与 AGENTS_FLOW 同序的定义列表（流程角色固定顺序 + definition_found 标记）。"""
    defs = _load_from_dir(root / ".opencode" / "agents", ".opencode/agents")
    if defs is None:
        # 仅当 opencode 目录不存在才回退；存在但为空不回退
        defs = _load_from_dir(root / ".claude" / "agents", ".claude/agents") or {}
    ordered = []
    for spec in AGENTS_FLOW:
        base = defs.pop(spec["name"], None)
        if base is None:
            base = {
                "name": spec["name"], "description": "", "mode": "", "color": "blue",
                "skills": [], "permission": None, "tools": "",
                "definition_found": False, "load_error": None,
                "source": None, "file": None,
            }
        base["role"] = spec["role"]
        base["optional"] = spec["optional"]
        base["stage"] = spec["stage"]
        ordered.append(base)
    # 流程外的定义（如有）追加在尾部，不丢弃
    for base in defs.values():
        base["role"] = ""
        base["optional"] = True
        base["stage"] = None
        ordered.append(base)
    return ordered
