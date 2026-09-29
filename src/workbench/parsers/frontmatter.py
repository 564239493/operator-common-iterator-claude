"""极简 YAML frontmatter 解析（仅覆盖 .claude/agents/*.md 的字段形态）。

不引入 PyYAML：只识别顶层 `key: value`、`- item` 列表与续行折叠，
对 agents frontmatter（name/description/tools/model/skills/color）足够。
"""


def parse_frontmatter(text):
    # type: (str) -> dict
    """从 markdown 文本解析 --- 包裹的 frontmatter，返回 dict。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)

    result = {}
    key = None
    for raw in body:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and key is not None:
            # 列表项，归属最近的 key
            if not isinstance(result.get(key), list):
                result[key] = []
            result[key].append(_scalar(stripped[2:]))
            continue
        if raw[:1] in (" ", "\t") and key is not None and isinstance(result.get(key), str):
            # 续行折叠进上一个标量
            result[key] = (result[key] + " " + stripped).strip()
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                result[key] = None  # 可能是后续列表的键
            else:
                result[key] = _scalar(value)
    # None 占位但无列表项的键置为空字符串，避免下游误判
    for k, v in list(result.items()):
        if v is None:
            result[k] = ""
    return result


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text
