"""极简 YAML frontmatter 解析。

支持：顶层标量、嵌套 map（任意层级缩进）、行内列表 [a, b]、`- item` 块列表、
带引号键/值、含冒号的值。未闭合 frontmatter 等结构错误返回 {"_error": msg}，
绝不让子键污染顶层。
"""


class FrontmatterError(ValueError):
    pass


def parse_frontmatter(text):
    # type: (str) -> dict
    """解析 --- 包裹的 frontmatter；结构错误返回 {"_error": ...}。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body = []
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        body.append(line)
    if not closed:
        return {"_error": "frontmatter 未闭合"}
    try:
        parsed, _ = _parse_block(body, 0, 0)
        return parsed
    except FrontmatterError as exc:
        return {"_error": str(exc)}


def _indent_of(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines, start, indent):
    # type: (list, int, int) -> tuple
    """解析缩进为 indent 的键值块，返回 (dict, 下一行号)。"""
    result = {}
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        cur = _indent_of(line)
        if cur < indent:
            break
        if cur > indent:
            raise FrontmatterError("意外的缩进: %r" % line.strip())
        stripped = line.strip()
        if stripped.startswith("- "):
            raise FrontmatterError("意外的列表项: %r" % stripped)
        key, value = _split_kv(stripped)
        key = _scalar(key)
        value = value.strip()
        i += 1
        if value == "":
            # 空值：看下一行是列表、嵌套 map 还是真空值
            if i < len(lines) and _indent_of(lines[i]) > cur:
                if lines[i].strip().startswith("- "):
                    result[key], i = _parse_list(lines, i, _indent_of(lines[i]))
                else:
                    result[key], i = _parse_block(lines, i, _indent_of(lines[i]))
            else:
                result[key] = ""
        else:
            result[key] = _parse_value(value)
    return result, i


def _parse_list(lines, start, indent):
    # type: (list, int, int) -> tuple
    items = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        cur = _indent_of(line)
        if cur < indent or not line.strip().startswith("- "):
            break
        items.append(_parse_value(line.strip()[2:].strip()))
        i += 1
    return items, i


def _split_kv(stripped):
    # type: (str) -> tuple
    """按第一个不在引号内的冒号切分键值。"""
    quote = None
    for idx, ch in enumerate(stripped):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ":":
            return stripped[:idx], stripped[idx + 1:]
    raise FrontmatterError("缺少冒号: %r" % stripped)


def _parse_value(value):
    # type: (str) -> object
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(item.strip()) for item in _split_inline(value[1:-1])]
    return _scalar(value)


def _split_inline(text):
    # type: (str) -> list
    parts = []
    buf = []
    quote = None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return [p.strip() for p in parts if p.strip()]


def _scalar(text):
    # type: (str) -> str
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text
