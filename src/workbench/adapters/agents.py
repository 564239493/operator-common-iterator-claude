"""GET /api/agents：解析 .claude/agents/*.md frontmatter + 固定流程顺序。"""
from ..parsers import frontmatter as fm
from .stage_inference import AGENTS_FLOW


def load_agent_defs(root):
    # type: (...) -> list
    """返回与 AGENTS_FLOW 同序的 agent 定义（静态描述 + frontmatter）。"""
    agents_dir = root / ".claude" / "agents"
    defs = {}
    if agents_dir.is_dir():
        for md in sorted(agents_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            meta = fm.parse_frontmatter(text)
            name = meta.get("name") or md.stem
            skills = meta.get("skills")
            if isinstance(skills, str):
                skills = [skills] if skills else []
            defs[name] = {
                "name": name,
                "description": meta.get("description") or "",
                "skills": skills or [],
                "tools": meta.get("tools") or "",
                "color": meta.get("color") or "blue",
                "file": ".claude/agents/%s" % md.name,
            }
    ordered = []
    for spec in AGENTS_FLOW:
        base = defs.pop(spec["name"], {"name": spec["name"], "description": "", "skills": [], "tools": "", "color": "blue", "file": None})
        base.update({"role": spec["role"], "optional": spec["optional"], "stage": spec["stage"]})
        ordered.append(base)
    # 流程外的 agent（若有）追加在尾部
    for name, base in defs.items():
        base.update({"role": "", "optional": True, "stage": None})
        ordered.append(base)
    return ordered
