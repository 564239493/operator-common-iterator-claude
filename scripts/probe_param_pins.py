#!/usr/bin/env python3
"""Detect list parameters whose length is coupled but has no upper bound.

背景：``array_length.value=[]`` 的列表参数走自由长度路径（没有 PICT length 因子，
``ListVar`` / ``TensorListVar`` 只补 ``Length >= 1``）；若参数又被跨参数 ``len(p)``
引用，生成器还会把长度释放为 Z3 自由变量。全程静默，长度没有上界，
量词/下标约束因此变慢甚至 UNKNOWN。

纯静态检查器（只用标准库 ``ast``/``re``，不 import ``agent`` 包，不跑 Z3）：

* ``probe``  —— 找出「长度未固定 + 被跨参数 len 引用 + 无传递上界」的参数，
  按 ``len(a) == len(b)`` 连通分量成组，输出 ``param_probe.json``（advisory）；
* ``patch``  —— 把用户确认过的 ``decisions`` 渲染成标准 ``constraints_patch``（list），
  交给 ``apply_supplement_constraints.py --origin param_pin`` 确定性合并；
* ``parse``  —— 按**闭集语法**解析用户在 AskUserQuestion 的 Other 里输入的文本
  （格式见 ``TEXT_FORMAT_HINTS``）。全有或全无：任一片段解析失败或取值非法即整句
  拒绝并回显格式。解析放在脚本里而非提示词里，“认出没认出”才是确定性、可单测的，
  模型只负责转述结果。带 ``--decisions-output`` 时成功即与已有 ``param_pins.json``
  合并落盘（同 ``group_id``/``leader`` 原位替换、其余保留），协调器无需抄 JSON。

检测范围刻意收窄（只报确定无上限）：没有跨参数 ``len()`` 引用的参数
（如 SolveTri 的 ``cuSeqlens``）只在 ``skipped`` 里留痕，不提问。
表达式正则/语义与生成器一致，复制自 ``param_constraint_utils.py`` 的
``_LEN_REF_RE`` 与跨参数判定、``combination_constraint_generate.py`` 的
``_FACTOR_REF_RE``。

exit code：文件/JSON 缺失或 ``parse`` 解析失败 → 2（失败时带 ``errors`` 与
``suggestions``，协调器回显后允许重填一次）；其余情况 → 0，本步骤不阻塞流程。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# 与 z3_expression_solver_utils.py 的 MAX_STATIC_UNROLL 对齐：长度不超过该值时生成器把
# all()/any() 展开成点对点子句，超过就继续用（慢的）量词。本脚本不 import 生成器，常量抄这儿。
UNROLL_LIMIT = 128

# 列表类参数：这些类型在生成侧走 ListVar / TensorListVar，长度可为 None。
LIST_TYPES = {
    "aclIntArray",
    "aclFloatArray",
    "aclBoolArray",
    "aclScalarList",
    "aclTensorList",
}
# aclFloatArray 在 constants.py:184 被映射成 ":attrs"（生成侧按 tensor 处理），
# 给它发长度约束不生效，只发 mapping_anomaly 警告。
TYPE_MAPPING_ANOMALY = {"aclFloatArray"}

# 跨参数长度耦合判定（与生成器同款）。
_LEN_REF_RE = re.compile(r"\blen\s*\(\s*([A-Za-z_]\w*)(?:\.range_value)?\s*\)")
_FACTOR_REF_RE = re.compile(
    r"(?<![a-zA-Z_])($|(?P<prev>[a-zA-Z_]\w*))\.(?P<attr>[a-zA-Z_]\w*)"
)

DEPRECATED_COMMON = "common"
ALL_PLATFORMS_SENTINEL = "all"

# ``parse`` 支持的输入格式（闭集）。拒绝消息原样回显这些写法，用户照着重填即可。
TEXT_FORMAT_HINTS = [
    "a=2",
    "a=[2,3] 或 a=2,3",
    "a=2~4 或 a=2-4",
    "a,b=2 或 a=b=2",
    "全部=2 或 统一=2",
]
# 单个参数一次给出的候选值上限：set 模式由 Z3 在集合内取值，集合过大没有意义。
LIST_MAX_VALUES = 16
ALL_SCOPE_NAMES = {"全部", "统一"}
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
_RANGE_RE = re.compile(r"^(\d+)\s*[-~]\s*(\d+)$")
# 全角/中文标点归一：用户从中文输入法打出来的符号占绝大多数，先归一再解析。
_FULLWIDTH_MAP = str.maketrans(
    {
        "＝": "=",
        "，": ",",
        "、": ",",
        "～": "~",
        "－": "-",
        "—": "-",
        "–": "-",
        "［": "[",
        "］": "]",
        "【": "[",
        "】": "]",
    }
)


def _force_utf8_stdio() -> None:
    """Windows GBK 控制台输出 JSON 时防 mojibake；只在 main() 调用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# 通用读取
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def _attr_value(attributes: dict, field: str):
    """取 ValueWithSrcText.value；缺失 / N/A / 空列表归一为 None。"""
    if not isinstance(attributes, dict):
        return None
    raw = attributes.get(field)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw in (None, "N/A", "N\\A"):
        return None
    if isinstance(raw, list) and not raw:
        return None
    return raw


def _type_name(attributes: dict) -> str:
    raw = attributes.get("type")
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\b(?:const|struct)\b|[*&]", "", raw).strip()


def _array_length_state(attributes: dict) -> str:
    """返回 unpinned / pinned / invalid。

    口径同 ``validate_artifacts.py``：``[]``/``N/A``/缺失 都算“没有长度约束”；
    ``[[]]`` 之类非法项在生成侧其实也退化成无长度因子，但多半是提取 bug，单独告警。
    """
    raw = attributes.get("array_length") if isinstance(attributes, dict) else None
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw in (None, "N/A", "N\\A") or raw == []:
        return "unpinned"
    if isinstance(raw, bool):
        return "invalid"
    if isinstance(raw, int):
        return "pinned"
    if isinstance(raw, list):
        if all(isinstance(item, int) and not isinstance(item, bool) for item in raw):
            return "pinned"
        # 区间形态 [[min, max]] 也是合法的“已给长度范围”。
        if (
            len(raw) == 1
            and isinstance(raw[0], list)
            and len(raw[0]) == 2
            and all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in raw[0]
            )
        ):
            return "pinned"
        return "invalid"
    return "invalid"


def _iter_param_attributes(value: dict):
    """遍历参数卡 ``{param: {platform: attrs}}``，yield (section, param, platform, attrs)。"""
    for section in ("inputs", "outputs"):
        params = value.get(section)
        if not isinstance(params, dict):
            continue
        for param, platforms in params.items():
            if not isinstance(platforms, dict):
                continue
            for platform, attributes in platforms.items():
                if isinstance(attributes, dict):
                    yield section, param, platform, attributes


def _cip(value: dict) -> dict[str, list[dict]] | None:
    """规范化 constraints_in_parameters；legacy list 形态返回 None。"""
    raw = value.get("constraints_in_parameters")
    if isinstance(raw, list):
        return None
    if not isinstance(raw, dict):
        return {}
    return {
        platform: list(items) if isinstance(items, list) else []
        for platform, items in raw.items()
    }


def _effective_constraints(cip: dict[str, list[dict]], platform: str) -> list[dict]:
    """目标平台桶 + common 桶（生成期会合并 common）。"""
    items: list[dict] = []
    items.extend(cip.get(DEPRECATED_COMMON, []))
    items.extend(cip.get(platform, []))
    return [item for item in items if isinstance(item, dict)]


def _expr_param_names(expr: str, param_names: set[str]) -> set[str]:
    names = {
        match.group("prev")
        for match in _FACTOR_REF_RE.finditer(expr)
        if match.group("prev")
    }
    names |= set(_LEN_REF_RE.findall(expr))
    return {name for name in names if name in param_names}


# ---------------------------------------------------------------------------
# 长度边界事实
# ---------------------------------------------------------------------------


def _len_target(node: ast.AST) -> str | None:
    """``len(p)`` / ``len(p.range_value)`` → p；其余返回 None。"""
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "len":
        return None
    if len(node.args) != 1:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    if (
        isinstance(arg, ast.Attribute)
        and arg.attr == "range_value"
        and isinstance(arg.value, ast.Name)
    ):
        return arg.value.id
    return None


def _int_literal(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
        node.value, bool
    ):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _int_list_literal(node: ast.AST) -> list[int] | None:
    if not isinstance(node, ast.List):
        return None
    values = [_int_literal(item) for item in node.elts]
    if not values or any(value is None for value in values):
        return None
    return [int(value) for value in values]  # type: ignore[arg-type]


def _expr_bound_facts(expr: str, param_names: set[str]) -> list[tuple]:
    """抽取长度边界事实：exact / upper / lower / set / equals。

    解析不了就静默返回空——语法问题归 ``validate_artifacts`` 管。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError, TypeError):
        return []
    facts: list[tuple] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            left_param = _len_target(left) if left is not None else None
            right_param = _len_target(right)
            left_int = _int_literal(left)
            right_int = _int_literal(right)
            left_set = _int_list_literal(left)
            right_set = _int_list_literal(right)

            if left_param and left_param in param_names:
                if right_param and right_param in param_names:
                    if isinstance(op, ast.Eq):
                        facts.append(("equals", left_param, right_param))
                elif right_int is not None:
                    if isinstance(op, ast.Eq):
                        facts.append(("exact", left_param, right_int))
                    elif isinstance(op, ast.LtE):
                        facts.append(("upper", left_param, right_int))
                    elif isinstance(op, ast.Lt):
                        facts.append(("upper", left_param, right_int - 1))
                    elif isinstance(op, ast.GtE):
                        facts.append(("lower", left_param, right_int))
                    elif isinstance(op, ast.Gt):
                        facts.append(("lower", left_param, right_int + 1))
                elif right_set is not None and isinstance(op, ast.In):
                    facts.append(("set", left_param, sorted(set(right_set))))
            elif right_param and right_param in param_names:
                if left_int is not None:
                    if isinstance(op, ast.Eq):
                        facts.append(("exact", right_param, left_int))
                    elif isinstance(op, ast.LtE):
                        facts.append(("lower", right_param, left_int))
                    elif isinstance(op, ast.Lt):
                        facts.append(("lower", right_param, left_int + 1))
                    elif isinstance(op, ast.GtE):
                        facts.append(("upper", right_param, left_int))
                    elif isinstance(op, ast.Gt):
                        facts.append(("upper", right_param, left_int - 1))
            left = right
    return facts


def _collect_bounds(
    cip: dict[str, list[dict]], platform: str, param_names: set[str]
) -> dict[str, dict]:
    bounds: dict[str, dict] = {}
    for item in _effective_constraints(cip, platform):
        expr = item.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            continue
        for fact in _expr_bound_facts(expr, param_names):
            kind, param = fact[0], fact[1]
            slot = bounds.setdefault(
                param,
                {"exact": set(), "upper": [], "lower": [], "set": [], "equals": set()},
            )
            if kind == "exact":
                slot["exact"].add(fact[2])
            elif kind == "upper":
                slot["upper"].append(fact[2])
            elif kind == "lower":
                slot["lower"].append(fact[2])
            elif kind == "set":
                slot["set"].append(list(fact[2]))
            elif kind == "equals":
                # 等长关系无向：两头都登记，上界才能沿 len(a)==len(b)==len(c) 链传下去。
                slot["equals"].add(fact[2])
                bounds.setdefault(
                    fact[2],
                    {"exact": set(), "upper": [], "lower": [], "set": [], "equals": set()},
                )["equals"].add(param)
    return bounds


def _has_upper_bound(
    param: str, bounds: dict[str, dict], seen: frozenset[str] = frozenset()
) -> bool:
    """可传递上界：直接 exact/upper/set，或经 ``len(p)==len(q)`` 传播。"""
    if param in seen:
        return False
    slot = bounds.get(param)
    if not slot:
        return False
    if slot["exact"] or slot["upper"] or slot["set"]:
        return True
    return any(
        _has_upper_bound(other, bounds, seen | {param}) for other in slot["equals"]
    )


def _zero_length_conflict(param: str, bounds: dict[str, dict]) -> bool:
    """``len(p) == 0`` 与 free ListVar 的 ``Length >= 1`` 必然冲突。"""
    slot = bounds.get(param)
    if not slot:
        return False
    if slot["exact"] == {0}:
        return True
    return any(values == [0] for values in slot["set"])


# ---------------------------------------------------------------------------
# 建议值：从约束下界与文本里的 batch 数量线索确定性推导
# ---------------------------------------------------------------------------
# 问询里的「推荐 2/3」不写死，尽量贴合当前算子的上下文；不额外加问询、不加模型调用。
_BATCH_HINT_RES = (
    re.compile(r"(?i)\bbatch\s*size\s*(?:=|＝|为|:|：)\s*(\d+)"),
    re.compile(r"(?i)batch\s*数\s*(?:为|=|＝|:|：)\s*(\d+)"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])B\s*(?:=|＝)\s*(\d+)"),
    re.compile(r"(\d+)\s*个\s*batch"),
)
_BATCH_CONTEXT_RE = re.compile(r"(?i)batch|批|(?<![A-Za-z0-9_])B\s*(?:=|＝)\s*\d")


def _group_text_hints(
    cip: dict[str, list[dict]], platforms: list[str], members: list[str], param_names: set[str]
) -> list[tuple[str, str]]:
    """组内参数相关约束的 expr 与文档原文，作为建议值的文本线索。"""
    member_set = set(members)
    texts: list[tuple[str, str]] = []
    for platform in platforms:
        for item in _effective_constraints(cip, platform):
            expr = item.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                continue
            if not _expr_param_names(expr, param_names) & member_set:
                continue
            constraint_id = item.get("id", "")
            label = f"约束 {constraint_id}（{platform}）" if constraint_id else f"约束（{platform}）"
            texts.append((expr, label))
            src_text = item.get("src_text")
            if isinstance(src_text, str) and src_text.strip():
                texts.append((src_text, f"{label}的文档原文"))
    return texts


def _batch_hint(texts: list[tuple[str, str]]) -> tuple[int, str] | None:
    """文本里出现的 batch 数量（``B = 4`` / ``batchSize 为 2`` / ``4 个 batch``）。"""
    for text, source in texts:
        if not _BATCH_CONTEXT_RE.search(text):
            continue
        for pattern in _BATCH_HINT_RES:
            match = pattern.search(text)
            if match:
                return int(match.group(1)), source
    return None


def _suggest_values(
    members: list[str],
    bounds_by_platform: dict[str, dict],
    hint: tuple[int, str] | None,
) -> tuple[list[int], list[str]]:
    """候选组的建议长度（推荐值在前）与依据。

    优先级：约束下界 > 文本里的 batch 数 > 默认 2/3。文档示例只有单 batch（``B = 1``）
    时不给 1、回落到 2/3——固定长度本来就是为了覆盖多 batch。
    """
    lower = 1
    for bounds in bounds_by_platform.values():
        for param in members:
            for value in (bounds.get(param) or {}).get("lower", []):
                lower = max(lower, int(value))

    values: list[int] = []
    basis: list[str] = []
    if lower > 1:
        values.append(lower)
        basis.append(f"约束已给出下界 len >= {lower}")
    if hint is not None:
        value, source = hint
        if value >= 2:
            values.append(value)
            basis.append(f"{source} 出现 batch 数 {value}")
        elif value == 1:
            basis.append(f"{source} 为单 batch 示例（batch 数 1），建议 2/3 覆盖多 batch")
    for value in (2, 3):
        if value not in values:
            values.append(value)
    if not basis:
        basis.append("无额外上下文线索，默认多 batch 常规值 2/3")
    return values[:3], basis


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _group_id(params: list[str], platforms: list[str]) -> str:
    payload = json.dumps(
        {"params": sorted(params), "platforms": sorted(platforms)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "G-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def build_probe(constraints_path: Path) -> dict:
    value = _load_json(constraints_path)
    if not isinstance(value, dict):
        raise ValueError("constraints.json 根节点必须是对象")

    cip = _cip(value)
    warnings: list[dict] = []
    skipped: list[dict] = []

    if cip is None:
        warnings.append(
            {
                "code": "legacy_cip_form",
                "detail": "constraints_in_parameters 为 list 形态（旧 schema），"
                "本探针只出报告，不生成长度补丁",
            }
        )
        cip = {}

    # 参数卡：param -> platform -> attrs；保持文档顺序。
    param_attrs: dict[str, dict[str, dict]] = {}
    for _section, param, platform, attributes in _iter_param_attributes(value):
        param_attrs.setdefault(param, {})[platform] = attributes
    param_names = set(param_attrs)

    # 每个平台的“跨参数 len 引用”证据 + 等长边。
    evidence: dict[str, list[dict]] = {}
    equality_edges: list[tuple[str, str]] = []
    for platform in cip:
        if platform == DEPRECATED_COMMON:
            continue
        for item in cip.get(platform, []):
            if not isinstance(item, dict):
                continue
            expr = item.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                continue
            names = _expr_param_names(expr, param_names)
            if len(names) < 2:
                continue
            for match in _LEN_REF_RE.finditer(expr):
                param = match.group(1)
                if param in param_names and (names - {param}):
                    evidence.setdefault(param, []).append(
                        {
                            "platform": platform,
                            "constraint_id": item.get("id", ""),
                            "expr": expr,
                        }
                    )
            for fact in _expr_bound_facts(expr, param_names):
                if fact[0] == "equals":
                    equality_edges.append((fact[1], fact[2]))

    # all()/any() 量词约束：长度固定成常数后生成器会定长展开（≤ UNROLL_LIMIT），
    # 超过上限仍按量词求解，所以选大长度要提示。
    quantifier_refs: list[dict] = []
    for platform in cip:
        if platform == DEPRECATED_COMMON:
            continue
        for item in cip.get(platform, []):
            if not isinstance(item, dict):
                continue
            expr = item.get("expr")
            if not isinstance(expr, str) or ("all(" not in expr and "any(" not in expr):
                continue
            quantifier_refs.append(
                {
                    "platform": platform,
                    "constraint_id": item.get("id", ""),
                    "expr": expr,
                    "names": _expr_param_names(expr, param_names),
                }
            )

    bounds_cache: dict[str, dict] = {}
    candidate_platforms: dict[str, set[str]] = {}
    for param, platforms in param_attrs.items():
        for platform, attributes in platforms.items():
            type_name = _type_name(attributes)
            if type_name not in LIST_TYPES:
                continue
            length_state = _array_length_state(attributes)
            if length_state == "pinned":
                skipped.append(
                    {"param": param, "platform": platform, "reason": "array_length_pinned"}
                )
                continue
            if length_state == "invalid":
                warnings.append(
                    {
                        "code": "invalid_length_entry",
                        "param": param,
                        "platform": platform,
                        "detail": "array_length 含非整数值，生成侧会退化为无长度因子",
                    }
                )
                skipped.append(
                    {"param": param, "platform": platform, "reason": "invalid_length_entry"}
                )
                continue
            if type_name in TYPE_MAPPING_ANOMALY:
                warnings.append(
                    {
                        "code": "mapping_anomaly",
                        "param": param,
                        "platform": platform,
                        "detail": f"{type_name} 映射异常（constants.py:184 → ':attrs'），"
                        "长度约束不会生效",
                    }
                )
                skipped.append(
                    {"param": param, "platform": platform, "reason": "mapping_anomaly"}
                )
                continue
            is_optional = _attr_value(attributes, "is_optional")
            if is_optional is None:
                skipped.append(
                    {"param": param, "platform": platform, "reason": "unknown_optional"}
                )
                continue
            if is_optional is True:
                skipped.append(
                    {"param": param, "platform": platform, "reason": "optional"}
                )
                continue
            is_operator_param = _attr_value(attributes, "is_operator_param")
            if is_operator_param is False:
                skipped.append(
                    {"param": param, "platform": platform, "reason": "not_operator_param"}
                )
                continue
            bounds = bounds_cache.get(platform)
            if bounds is None:
                bounds = _collect_bounds(cip, platform, param_names)
                bounds_cache[platform] = bounds
            # 先判 len(p)==0 冲突（它也会命中 has_upper_bound，得优先告警），再判上界。
            # 上界判定放在引用判定之前：已固定长度的参数就算没有跨参数 len 引用，
            # 也该如实报 has_upper_bound，而不是 not_len_referenced。
            if _zero_length_conflict(param, bounds):
                warnings.append(
                    {
                        "code": "zero_length_conflict",
                        "param": param,
                        "platform": platform,
                        "detail": "len(p) == 0 与生成器 Length >= 1 冲突；"
                        "需要空数组请改 array_length=[0]",
                    }
                )
                skipped.append(
                    {"param": param, "platform": platform, "reason": "zero_length_conflict"}
                )
                continue
            if _has_upper_bound(param, bounds):
                skipped.append(
                    {"param": param, "platform": platform, "reason": "has_upper_bound"}
                )
                continue
            if param not in evidence or not any(
                item["platform"] in (platform, DEPRECATED_COMMON)
                for item in evidence[param]
            ):
                skipped.append(
                    {"param": param, "platform": platform, "reason": "not_len_referenced"}
                )
                continue
            candidate_platforms.setdefault(param, set()).add(platform)

    # 等长组：只在候选参数之间连边，避免把已有上界的参数拉进组。
    candidates = set(candidate_platforms)
    union = _UnionFind()
    for left, right in equality_edges:
        if left in candidates and right in candidates:
            union.union(left, right)
    components: dict[str, list[str]] = {}
    for param in candidates:
        components.setdefault(union.find(param), []).append(param)

    doc_order = {param: index for index, param in enumerate(param_attrs)}
    groups: list[dict] = []
    for members in components.values():
        members.sort(key=lambda item: doc_order.get(item, 0))
        platforms = sorted(
            {platform for param in members for platform in candidate_platforms[param]}
        )
        leader = members[0]
        group_evidence: list[str] = []
        for param in members:
            for item in evidence.get(param, []):
                if item["platform"] not in platforms:
                    continue
                label = f"{item['constraint_id']}: {item['expr']}" if item["constraint_id"] else item["expr"]
                if label not in group_evidence:
                    group_evidence.append(label)
        group_quantifiers: list[str] = []
        for item in quantifier_refs:
            if item["platform"] not in platforms or not item["names"] & set(members):
                continue
            label = f"{item['constraint_id']}: {item['expr']}" if item["constraint_id"] else item["expr"]
            if label not in group_quantifiers:
                group_quantifiers.append(label)
        group_bounds = {platform: bounds_cache[platform] for platform in platforms}
        suggested, suggestion_basis = _suggest_values(
            members,
            group_bounds,
            _batch_hint(_group_text_hints(cip, platforms, members, param_names)),
        )
        groups.append(
            {
                "group_id": _group_id(members, platforms),
                "params": members,
                "leader": leader,
                "platforms": platforms,
                "type": _type_name(param_attrs[leader][platforms[0]]),
                "array_length": [],
                "evidence": group_evidence[:5],
                "quantifier_constraints": group_quantifiers[:5],
                "suggested_values": suggested,
                "suggestion_basis": suggestion_basis,
                "upper_bound": None,
                "reason": "free_length_coupled",
                "askable": True,
            }
        )
    groups.sort(key=lambda group: doc_order.get(group["leader"], 0))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "constraints_file": str(constraints_path.resolve()),
        "candidates": groups,
        "skipped": skipped,
        "warnings": warnings,
        "summary": {
            "n_candidates": sum(len(group["params"]) for group in groups),
            "n_groups": len(groups),
            "n_skip": len(skipped),
            "n_warnings": len(warnings),
        },
    }


# ---------------------------------------------------------------------------
# 决策 → patch
# ---------------------------------------------------------------------------


def _load_decisions(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    value = _load_json(path)
    if isinstance(value, dict):
        value = value.get("decisions")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def merge_decisions(existing: list[dict], new: list[dict]) -> list[dict]:
    """把本轮决策并入已有 param_pins.json：同 group_id/leader 原位替换，其余保留。"""
    by_key: dict[str, dict] = {}
    for item in new:
        for key in (str(item.get("group_id") or ""), str(item.get("leader") or "")):
            if key:
                by_key[key] = item
    merged: list[dict] = []
    consumed: set[int] = set()
    for item in existing:
        hit = None
        for key in (str(item.get("group_id") or ""), str(item.get("leader") or "")):
            if key and key in by_key:
                hit = by_key[key]
                break
        if hit is None:
            merged.append(item)
        else:
            # 本轮决策缺 group_id/params 时沿用旧条目的标识，避免合并后审计字段丢失。
            filled = dict(hit)
            for field in ("group_id", "params"):
                if not filled.get(field) and item.get(field):
                    filled[field] = item[field]
            merged.append(filled)
            consumed.add(id(hit))
    merged.extend(item for item in new if id(item) not in consumed)
    return merged


def _decision_value(decision: dict) -> tuple[str, list[int]] | None:
    """把决策归一为 (mode, 值)；mode 为空表示跳过。"""
    mode = str(decision.get("mode") or "").strip().lower()
    if mode in ("", "auto", "skip"):
        return None
    if mode == "fix":
        length = decision.get("length")
        if isinstance(length, int) and not isinstance(length, bool) and length >= 1:
            return "fix", [length]
        return None
    if mode == "set":
        lengths = decision.get("lengths")
        if isinstance(lengths, list):
            values = sorted(
                {
                    item
                    for item in lengths
                    if isinstance(item, int) and not isinstance(item, bool) and item >= 1
                }
            )
            if len(values) == 1:
                return "fix", values
            if len(values) >= 2:
                return "set", values
        return None
    if mode == "range":
        lower = decision.get("min_length")
        upper = decision.get("max_length")
        if (
            isinstance(lower, int)
            and isinstance(upper, int)
            and not isinstance(lower, bool)
            and not isinstance(upper, bool)
            and 1 <= lower <= upper
        ):
            return "range", [lower, upper]
        return None
    return None


def _render_expr(mode: str, leader: str, values: list[int]) -> str:
    if mode == "fix":
        return f"len({leader}) == {values[0]}"
    if mode == "set":
        return f"len({leader}) in [{', '.join(str(item) for item in values)}]"
    return f"{values[0]} <= len({leader}) <= {values[1]}"


def _canonical_expr(expr: str) -> str:
    try:
        return ast.dump(ast.parse(expr, mode="eval"), include_attributes=False)
    except (SyntaxError, ValueError, TypeError):
        return " ".join(expr.split())


def _platform_targets(
    decision: dict,
    leader: str,
    cip: dict[str, list[dict]],
    param_attrs: dict[str, dict[str, dict]],
) -> list[str]:
    requested = decision.get("platform") or ALL_PLATFORMS_SENTINEL
    if requested != ALL_PLATFORMS_SENTINEL:
        return [str(requested)]
    platforms = [key for key in cip if key != DEPRECATED_COMMON]
    if not platforms:
        return []
    # 只有 leader 在全部平台都出现时才用 "all"，避免给不存在的参数发约束。
    leader_platforms = param_attrs.get(leader, {})
    if all(platform in leader_platforms for platform in platforms):
        return [ALL_PLATFORMS_SENTINEL]
    return [platform for platform in platforms if platform in leader_platforms]


def build_patch(constraints_path: Path, decisions_path: Path | None) -> dict:
    value = _load_json(constraints_path)
    if not isinstance(value, dict):
        raise ValueError("constraints.json 根节点必须是对象")
    cip = _cip(value) or {}
    toplevel_warnings: list[dict] = []
    if isinstance(value.get("constraints_in_parameters"), list):
        toplevel_warnings.append(
            {
                "code": "legacy_cip_form",
                "detail": "constraints_in_parameters 为 list 形态，无法展开 target_platform=all",
            }
        )

    param_attrs: dict[str, dict[str, dict]] = {}
    for _section, param, platform, attributes in _iter_param_attributes(value):
        param_attrs.setdefault(param, {})[platform] = attributes
    param_names = set(param_attrs)

    probe = build_probe(constraints_path)
    groups = {group["group_id"]: group for group in probe["candidates"]}
    group_by_leader = {group["leader"]: group for group in probe["candidates"]}
    group_by_param = {
        param: group
        for group in probe["candidates"]
        for param in group["params"]
    }

    patch: list[dict] = []
    applied: list[dict] = []
    warnings: list[dict] = list(toplevel_warnings)

    for decision in _load_decisions(decisions_path):
        parsed = _decision_value(decision)
        if parsed is None:
            continue
        mode, values = parsed
        group = (
            groups.get(str(decision.get("group_id") or ""))
            or group_by_leader.get(str(decision.get("leader") or ""))
            or next(
                (
                    group_by_param[param]
                    for param in decision.get("params") or []
                    if param in group_by_param
                ),
                None,
            )
        )
        if group is None:
            warnings.append(
                {
                    "code": "unknown_group",
                    "detail": f"决策无法匹配当前候选组：{decision!r}",
                }
            )
            continue
        leader = str(decision.get("leader") or group["leader"])
        if leader not in group["params"]:
            leader = group["leader"]
        expr = _render_expr(mode, leader, values)
        if max(values) > UNROLL_LIMIT and group.get("quantifier_constraints"):
            warnings.append(
                {
                    "code": "large_length_quantifier",
                    "param": leader,
                    "detail": f"选择长度 {max(values)} 且组内使用 all()/any() 量词，"
                    f"超过定长展开上限 {UNROLL_LIMIT}，仍按量词求解，可能明显变慢",
                }
            )

        targets = _platform_targets(decision, leader, cip, param_attrs)
        if not targets:
            warnings.append(
                {
                    "code": "no_platform",
                    "param": leader,
                    "detail": "constraints_in_parameters 没有可展开的平台桶",
                }
            )
            continue

        for target in targets:
            bucket_key = target if target != ALL_PLATFORMS_SENTINEL else None
            buckets = (
                [key for key in cip if key != DEPRECATED_COMMON]
                if bucket_key is None
                else [bucket_key]
            )
            conflict = False
            already = False
            for key in buckets:
                for item in cip.get(key, []):
                    if not isinstance(item, dict):
                        continue
                    existing_expr = item.get("expr")
                    if not isinstance(existing_expr, str):
                        continue
                    if _canonical_expr(existing_expr) == _canonical_expr(expr):
                        already = True
                        break
                if already:
                    break
            if already:
                continue

            # 与已有精确上界冲突时跳过（探测本不该在这种参数上提问）。
            for key in buckets:
                bounds = _collect_bounds(cip, key, param_names)
                slot = bounds.get(leader, {})
                exact = slot.get("exact") or set()
                if exact and not exact.issubset(set(values)):
                    conflict = True
                    warnings.append(
                        {
                            "code": "conflict_existing_exact",
                            "param": leader,
                            "platform": key,
                            "detail": f"已有 len({leader}) == {sorted(exact)}，与选择 {values} 冲突",
                        }
                    )
                    break
                sets = slot.get("set") or []
                if sets and not set(values).issubset(set(sets[0])):
                    conflict = True
                    warnings.append(
                        {
                            "code": "conflict_existing_set",
                            "param": leader,
                            "platform": key,
                            "detail": f"已有 len({leader}) in {sets[0]}，与选择 {values} 冲突",
                        }
                    )
                    break
            if conflict:
                continue

            patch.append(
                {
                    "op": "add_constraint",
                    "target_platform": target,
                    "proposed": {
                        "expr_type": "self_shape_dim_range",
                        "expr": expr,
                        "relation_params": [leader],
                    },
                    "basis": (
                        "用户确认（param pin probe）："
                        f"group={group['group_id']}; params={group['params']}; "
                        f"选择 {mode}={values}; 依据 "
                        + "; ".join(group.get("evidence", [])[:2])
                    ),
                }
            )
            applied.append(
                {
                    "group_id": group["group_id"],
                    "leader": leader,
                    "expr": expr,
                    "target_platform": target,
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "constraints_file": str(constraints_path.resolve()),
        "patch": patch,
        "applied": applied,
        "warnings": warnings,
        "summary": {"n_patch": len(patch), "n_warnings": len(warnings)},
    }


# ---------------------------------------------------------------------------
# 文本决策解析（Other 自由输入 → decisions）
# ---------------------------------------------------------------------------


def _error(fragment: str, code: str, message: str, hint: str | None = None) -> dict:
    item = {"fragment": fragment, "code": code, "message": message}
    if hint:
        item["hint"] = hint
    return item


def _normalize_text(text: str) -> str:
    """全角转半角、中文标点归一、去掉运算符两侧空白；不做任何语义改写。"""
    if not isinstance(text, str):
        return ""
    value = unicodedata.normalize("NFKC", text).translate(_FULLWIDTH_MAP)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*([=,\[\]~\-])\s*", r"\1", value)
    return value.strip()


def _split_segments(text: str) -> list[str]:
    """按**方括号外**的逗号切段；``a=[2,3]`` 里的逗号不切。"""
    segments: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            segments.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(char)
    segments.append("".join(buffer).strip())
    return segments


def _parse_spec(spec: str) -> tuple[list[int] | None, dict | None]:
    """解析取值：``2`` / ``2,3`` / ``[2,3]`` / ``2~4`` / ``2-4``。"""
    text = spec.strip()
    if not text:
        return None, _error("", "missing_value", "缺少取值")
    if text.startswith("[") or text.endswith("]"):
        if not (text.startswith("[") and text.endswith("]")):
            return None, _error(text, "bracket_mismatch", "方括号没有成对出现",
                                "多值请写成 a=[2,3]")
        inner = text[1:-1].strip()
        items = inner.split(",") if inner else []
    else:
        items = text.split(",")
    values: list[int] = []
    for raw in items:
        item = raw.strip()
        if not item:
            return None, _error(text, "empty_value", "取值里出现空项",
                                "多值请写成 a=[2,3] 或 a=2,3")
        range_match = _RANGE_RE.match(item)
        if range_match:
            lower, upper = int(range_match.group(1)), int(range_match.group(2))
            if lower < 1 or upper < lower:
                return None, _error(text, "invalid_range",
                                    f"范围 {item} 非法（下界须 ≥1，且下界不能大于上界）")
            if upper - lower + 1 > LIST_MAX_VALUES:
                return None, _error(text, "range_too_wide",
                                    f"范围 {item} 展开后超过 {LIST_MAX_VALUES} 个候选值")
            values.extend(range(lower, upper + 1))
            continue
        if not item.isdigit():
            return None, _error(text, "invalid_value",
                                f"取值 '{item}' 不是 ≥1 的整数、范围或方括号列表")
        value = int(item)
        if value < 1:
            return None, _error(text, "invalid_length", "长度必须是 ≥1 的整数")
        values.append(value)
    deduped = list(dict.fromkeys(values))
    if len(deduped) > LIST_MAX_VALUES:
        return None, _error(text, "list_too_long",
                            f"候选值超过 {LIST_MAX_VALUES} 个")
    return deduped, None


def _parse_assignments(text: str) -> tuple[list[dict], list[dict]]:
    """把归一后的文本切成「参数名列表 + 取值」。

    逗号后紧跟 ``<参数名>=`` 才是新赋值，否则并进上一个赋值的取值列表
    （``a=2,3`` → a 取 2、3；``a=2, b=3`` → a=2、b=3）。
    """
    assignments: list[dict] = []
    errors: list[dict] = []
    pending: list[str] = []
    for segment in _split_segments(text):
        if not segment:
            errors.append(_error("", "empty_fragment", "出现空的赋值片段（多打了逗号？）",
                                 "示例：" + "；".join(TEXT_FORMAT_HINTS)))
            continue
        if "=" in segment:
            parts = [part.strip() for part in segment.split("=")]
            names = pending + parts[:-1]
            spec = parts[-1]
            pending = []
            if not names:
                errors.append(_error(segment, "missing_param", f"'{segment}' 缺少参数名"))
                continue
            bad = [name for name in names
                   if not _IDENT_RE.match(name) and name not in ALL_SCOPE_NAMES]
            if bad:
                errors.append(_error(segment, "invalid_param_name",
                                     f"参数名无法识别：{', '.join(bad)}",
                                     "参数名取 param_probe.json 里的 leader/params"))
                continue
            if not spec:
                errors.append(_error(segment, "missing_value",
                                     f"{', '.join(names)} 缺少取值",
                                     "示例：" + "；".join(TEXT_FORMAT_HINTS)))
                continue
            assignments.append({"names": names, "spec": spec, "fragments": [segment]})
            continue
        if _IDENT_RE.match(segment) or segment in ALL_SCOPE_NAMES:
            # 裸参数名（``a,b=2`` 的前半段），等下一个带 ``=`` 的片段。
            pending.append(segment)
            continue
        if assignments:
            # 纯取值片段：续接到上一个赋值的取值列表（``a=2,3,4``）。
            assignments[-1]["spec"] += "," + segment
            assignments[-1]["fragments"].append(segment)
            continue
        errors.append(_error(segment, "unrecognized", f"无法识别的片段：'{segment}'",
                             "示例：" + "；".join(TEXT_FORMAT_HINTS)))
    if pending:
        errors.append(_error(",".join(pending), "missing_value",
                             f"{', '.join(pending)} 缺少取值",
                             "示例：" + "；".join(TEXT_FORMAT_HINTS)))
    return assignments, errors


def _parse_group_index(probe: dict | None) -> tuple[dict[str, dict], list[dict]]:
    """param_probe.json → (参数名索引, 组顺序)；未提供 probe 时为空。"""
    index: dict[str, dict] = {}
    groups: list[dict] = []
    if not isinstance(probe, dict):
        return index, groups
    for group in probe.get("candidates") or []:
        if not isinstance(group, dict):
            continue
        leader = str(group.get("leader") or "")
        members = [str(item) for item in group.get("params") or []]
        if leader and leader not in members:
            members.insert(0, leader)
        if not members:
            continue
        entry = {
            "group_id": str(group.get("group_id") or ""),
            "leader": leader or members[0],
            "params": members,
            "askable": bool(group.get("askable", True)),
        }
        groups.append(entry)
        for name in members:
            index[name] = entry
    return index, groups


def parse_text_decisions(text: str, probe: dict | None = None) -> dict:
    """把自由文本解析成 ``decisions``；全有或全无（有错则 decisions 为空）。"""
    normalized = _normalize_text(text)
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "text": text,
        "normalized": normalized,
        "decisions": [],
        "warnings": [],
        "errors": [],
        "suggestions": list(TEXT_FORMAT_HINTS),
    }
    if not normalized:
        report["errors"].append(
            _error("", "empty_input", "没有收到可解析的内容",
                   "示例：" + "；".join(TEXT_FORMAT_HINTS))
        )
        return report

    assignments, errors = _parse_assignments(normalized)
    report["errors"].extend(errors)

    scope_items = [
        item for item in assignments
        if all(name in ALL_SCOPE_NAMES for name in item["names"])
    ]
    named_items = [
        item for item in assignments
        if not all(name in ALL_SCOPE_NAMES for name in item["names"])
    ]
    if scope_items and named_items:
        # “全部=2” 与逐个参数写法混用含义不明确（是覆盖还是并列？），整句拒绝。
        fragment = ",".join(
            part for item in assignments for part in item["fragments"]
        )
        report["errors"].append(
            _error(fragment, "mixed_scope",
                   "“全部/统一”不能和其他参数名混用",
                   "全部统一时只写 全部=2；只改个别参数时逐个列出")
        )
        assignments = []

    index, all_groups = _parse_group_index(probe)
    grouped: dict[str, dict] = {}
    for assignment in assignments:
        fragment = ",".join(assignment["fragments"])
        values, spec_error = _parse_spec(assignment["spec"])
        if spec_error is not None:
            spec_error["fragment"] = fragment
            report["errors"].append(spec_error)
            continue
        names = assignment["names"]
        if all(name in ALL_SCOPE_NAMES for name in names):
            targets = [entry for entry in all_groups if entry["askable"]]
            if not targets:
                report["errors"].append(
                    _error(fragment, "no_askable_group",
                           "没有可应用的候选组（未提供 --probe，或本轮没有待问的组）",
                           "请改为逐个参数写，如 a=2")
                )
                continue
        else:
            if any(name in ALL_SCOPE_NAMES for name in names):
                report["errors"].append(
                    _error(fragment, "mixed_scope",
                           "“全部/统一”不能和其他参数名混用",
                           "全部统一时只写 全部=2；只改个别参数时逐个列出")
                )
                continue
            targets = []
            for name in names:
                entry = index.get(name) if probe is not None else None
                if probe is not None and entry is None:
                    report["errors"].append(
                        _error(fragment, "unknown_param",
                               f"参数 {name} 不在本轮候选组内",
                               "可写参数名见题面或 param_probe.json 的 params")
                    )
                    continue
                if entry is None:
                    entry = {"group_id": "", "leader": name,
                             "params": [name], "askable": True}
                targets.append(entry)
        for entry in targets:
            key = entry["group_id"] or entry["leader"]
            record = grouped.get(key)
            if record is None:
                grouped[key] = {"entry": entry, "values": values,
                                "source": fragment}
            elif record["values"] != values:
                report["errors"].append(
                    _error(fragment, "group_conflict",
                           f"参数组 {entry['params']} 出现两种取值："
                           f"{record['values']} 与 {values}",
                           "同一等长组只能给一个取值")
                )

    order = [entry["group_id"] or entry["leader"] for entry in all_groups]
    ordered_keys = [key for key in order if key in grouped]
    ordered_keys += [key for key in grouped if key not in ordered_keys]
    for key in ordered_keys:
        record = grouped[key]
        entry = record["entry"]
        values = record["values"]
        decision = {
            "group_id": entry["group_id"],
            "params": list(entry["params"]),
            "leader": entry["leader"],
            "platform": ALL_PLATFORMS_SENTINEL,
            "decided_by": "user",
            "source": record["source"],
        }
        if len(values) == 1:
            decision["mode"] = "fix"
            decision["length"] = values[0]
        else:
            decision["mode"] = "set"
            decision["lengths"] = values
        report["decisions"].append(decision)
        if max(values) > UNROLL_LIMIT:
            report["warnings"].append(
                {
                    "code": "large_length_quantifier",
                    "leader": entry["leader"],
                    "detail": f"长度 {max(values)} 超过定长展开上限 {UNROLL_LIMIT}，"
                    "生成期仍按量词求解，可能明显变慢",
                }
            )

    report["ok"] = not report["errors"]
    if not report["ok"]:
        # 全有或全无：有任何片段没解析出来，整句不应用，避免半生效。
        report["decisions"] = []
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect unbounded list-parameter lengths and turn user decisions "
        "into a constraints_patch (advisory, deterministic)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="scan constraints.json for unbounded list lengths")
    probe.add_argument("constraints", help="path to constraints.json")
    probe.add_argument("--output", help="write param_probe.json here (default: stdout)")
    probe.add_argument(
        "--decisions",
        help="optional param_pins.json; groups already decided are not re-asked",
    )

    patch = sub.add_parser("patch", help="turn decisions into constraints_patch list")
    patch.add_argument("constraints", help="path to constraints.json")
    patch.add_argument("--decisions", required=True, help="path to param_pins.json")
    patch.add_argument("--output", help="write patch JSON here (default: stdout)")

    parse = sub.add_parser(
        "parse",
        help="parse free-form user text into decisions (all-or-nothing)",
    )
    parse.add_argument("--text", required=True, help="user input as typed (Other field)")
    parse.add_argument(
        "--probe",
        help="optional param_probe.json; validates param names and maps them to groups",
    )
    parse.add_argument("--output", help="write parse report here (default: stdout)")
    parse.add_argument(
        "--decisions",
        help="existing param_pins.json merged with this round's decisions "
        "(same group_id/leader is replaced)",
    )
    parse.add_argument(
        "--decisions-output",
        help="when parsing succeeds, write merged param_pins.json here "
        "(not written when parsing fails)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = _build_parser().parse_args(argv)
    if args.command == "parse":
        probe: dict | None = None
        if args.probe:
            probe_path = Path(args.probe)
            if not probe_path.is_file():
                print(
                    json.dumps(
                        {"ok": False, "code": "PROBE_NOT_FOUND",
                         "path": str(probe_path)},
                        ensure_ascii=False,
                    )
                )
                return 2
            try:
                probe = _load_json(probe_path)
            except json.JSONDecodeError as exc:
                print(
                    json.dumps(
                        {"ok": False, "code": "INVALID_PROBE", "detail": str(exc)},
                        ensure_ascii=False,
                    )
                )
                return 2
            if not isinstance(probe, dict):
                print(
                    json.dumps(
                        {"ok": False, "code": "INVALID_PROBE",
                         "detail": "param_probe.json 根节点必须是对象"},
                        ensure_ascii=False,
                    )
                )
                return 2
        report = parse_text_decisions(args.text, probe)
        decisions_written: str | None = None
        if args.decisions_output and report["ok"]:
            # 与已有 param_pins.json 合并后再落盘：同组决策原位替换，跨迭代的其它组保留，
            # 避免本轮覆盖历史决策导致已裁决的组被重复提问。
            merged = merge_decisions(
                _load_decisions(Path(args.decisions) if args.decisions else None),
                report["decisions"],
            )
            _write_json_atomic(
                Path(args.decisions_output),
                {"schema_version": SCHEMA_VERSION, "decisions": merged},
            )
            decisions_written = str(Path(args.decisions_output).resolve())
        if args.output:
            _write_json_atomic(Path(args.output), report)
            print(
                json.dumps(
                    {
                        "ok": report["ok"],
                        "output": str(Path(args.output).resolve()),
                        "decisions_output": decisions_written,
                        "n_decisions": len(report["decisions"]),
                        "n_errors": len(report["errors"]),
                    },
                    ensure_ascii=False,
                )
            )
        elif decisions_written:
            print(
                json.dumps(
                    {
                        "ok": report["ok"],
                        "decisions_output": decisions_written,
                        "n_decisions": len(report["decisions"]),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2

    constraints_path = Path(args.constraints)
    if not constraints_path.is_file():
        print(
            json.dumps(
                {"ok": False, "code": "CONSTRAINTS_NOT_FOUND",
                 "path": str(constraints_path)},
                ensure_ascii=False,
            )
        )
        return 2
    try:
        if args.command == "probe":
            report = build_probe(constraints_path)
            decided = {
                str(item.get("group_id") or item.get("leader") or "")
                for item in _load_decisions(
                    Path(args.decisions) if args.decisions else None
                )
            }
            if decided:
                for group in report["candidates"]:
                    if group["group_id"] in decided or group["leader"] in decided:
                        group["askable"] = False
                        group["reason"] = "already_decided"
            if args.output:
                _write_json_atomic(Path(args.output), report)
                print(
                    json.dumps(
                        {"ok": True, "output": str(Path(args.output).resolve()),
                         "summary": report["summary"]},
                        ensure_ascii=False,
                    )
                )
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        result = build_patch(
            constraints_path,
            Path(args.decisions) if args.decisions else None,
        )
        if args.output:
            _write_json_atomic(Path(args.output), result["patch"])
            print(
                json.dumps(
                    {
                        "ok": True,
                        "output": str(Path(args.output).resolve()),
                        "summary": result["summary"],
                        "applied": result["applied"],
                        "warnings": result["warnings"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"ok": False, "code": "INVALID_INPUT", "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
