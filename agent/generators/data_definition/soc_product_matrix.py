#!/usr/bin/env python3
"""SoC 名(芯片型号 / SocVersion 枚举) -> 算子产品名 映射表 + 规范化匹配。

供 source-analyst（产 constraints_patch.json 时按 soc_scope 设 target_platform）与
apply 校验共用,纯数据 + 纯函数,不含业务推理。遵循 CLAUDE.md "Python 只负责确定性"。

数据来源:用户提供的产品-芯片对应表(2026-07-09):

| 产品名称                                | 具体芯片型号                                                                        |
| ----------------------------------- | ----------------------------------------------------------------------------- |
| Atlas 350                            | Ascend950PR                                                                   |
| Atlas A3 训练系列/Atlas A3 推理系列          | Ascend910C1/C2/C3/C4                                                          |
| Atlas A2 训练系列/Atlas A2 推理系列          | Ascend910B1/B2/B3/B4                                                          |
| Atlas 200I/500 A2 推理产品               | Ascend310B3/B4                                                                |
| Atlas 推理系列加速卡产品                      | Ascend910A/B, Ascend910ProA/B, Ascend910PremiumA                             |
| Atlas 训练系列                          | Ascend910/A/B, Ascend910ProA/B, Ascend910PremiumA                             |

源码侧 SoC 分支两类写法(见 extract_source_constraints.extract_soc_branches):
  - aclrtGetSocName() 返回字符串比较: "Ascend910B3" / "Ascend950PR"
  - SocVersion 枚举比较: ASCEND910B / ASCEND310P / ASCEND910C / ASCEND950

映射策略三跳: soc token -> canonical_soc -> 芯片族 -> 候选产品族规范键 ->
在 constraints.json 的 product_support 列表里做规范化子串匹配,命中返回原字符串。

命名不一致的已知点(故用规范化匹配,不硬编码字符串相等):
  - RunPlatform 枚举(param_models_def.py)里 Atlas 350 = "Accelerator Card"(英文),
    但 constraints.json 实际 key 是 "Atlas 350 加速卡"(中文)。
  - constants.PLATFORM_MAP 缺 Atlas 350;措辞"系列产品"vs 用户表"加速卡产品"/"系列"略异。
生成器遍历 constraints.json 的 product_support(不校验枚举),target_platform 须逐字符匹配
product_support 项 -> 映射表产品端以 product_support 实际项为准做规范化匹配,不依赖枚举值。

芯片族与产品族重叠(Ascend910/Ascend910A/Ascend910Pro/Ascend910Premium 在"推理加速卡"与
"训练系列"两族都有) -> 映射候选多值,由 product_support 实际项收敛:命中几个产几个 patch。
"""

from __future__ import annotations

import re

# 芯片族规范键 -> 候选产品族规范键列表(已 canonical_product 规范化)。
# 芯片族键为"去尾号纯数字后的族"(ascend910b/ascend910c/ascend910a/ascend910/ascend910pro/
# ascend910premium/ascend310b/ascend950);ascend950pr 经前缀回退命中 ascend950。
SOC_FAMILY_TO_PRODUCT_FAMILIES: dict[str, list[str]] = {
    "ascend950": ["atlas350"],
    "ascend910c": ["atlasa3"],
    "ascend910b": ["atlasa2"],
    "ascend310b": ["atlas200i/500a2"],
    # Ascend910A / Ascend910 / Ascend910Pro / Ascend910Premium 在"推理加速卡"与"训练系列"
    # 两族芯片重叠 -> 候选两族,由 product_support 实际项收敛。
    "ascend910a": ["atlas推理", "atlas训练"],
    "ascend910": ["atlas推理", "atlas训练"],
    "ascend910pro": ["atlas推理", "atlas训练"],
    "ascend910premium": ["atlas推理", "atlas训练"],
}

# SoC token 规范化:小写(保留 ascend 前缀,与族表 key 一致)、去"族字母后的子型号尾号单数字"。
# Ascend910B3 -> ascend910b(B 族,3 子型号去); Ascend910 -> ascend910(无族字母,910 主体不去);
# Ascend910C2 -> ascend910c; Ascend310B4 -> ascend310b。字母尾缀(pr/p/premium/pro)保留:
# Ascend950PR -> ascend950pr(族表经前缀回退命中 ascend950); Ascend310P -> ascend310p(不在表->unknown)。
# (?<=[a-zA-Z])\d$ 只去"前一字符是字母"的末尾单数字,避免把 Ascend910 的"910"误去(前一是数字)。
_TRAIL_DIGITS_RE = re.compile(r'(?<=[a-zA-Z])\d$')


def canonical_soc(token: str) -> str:
    """规范化 SoC token:Ascend910B3/ASCEND910B -> ascend910b;Ascend950PR -> ascend950pr;
    ASCEND310P -> ascend310p(不在表 -> unknown);Ascend910 -> ascend910。"""
    if not token:
        return ""
    s = token.strip().lower()  # 保留 ascend 前缀,与族表 key 一致
    s = re.sub(r'\s+', '', s)
    # 去末尾单数字,仅当其前一字符是字母(族字母 a/b/c 后的子型号 1-4);910 主体数字不去
    s = _TRAIL_DIGITS_RE.sub('', s)
    return s


# 产品名规范化后缀词(长在前,避免"加速卡产品"被"产品"先吃):
# "Atlas A2 训练系列产品/Atlas A2 推理系列产品" -> "atlasa2训练/atlasa2推理"(候选"atlasa2"子串匹配)
# "Atlas 350 加速卡" -> "atlas350"; "Atlas 推理系列加速卡产品" -> "atlas推理"
_PRODUCT_SUFFIX_RE = re.compile(r'(加速卡产品|系列产品|加速卡|产品|系列)')
_PRODUCT_SPACE_RE = re.compile(r'\s+')


def canonical_product(name: str) -> str:
    """规范化产品名:小写+去空格+去后缀词(加速卡/系列产品/产品/系列),供子串匹配。"""
    if not name:
        return ""
    s = name.lower()
    s = _PRODUCT_SPACE_RE.sub('', s)
    s = _PRODUCT_SUFFIX_RE.sub('', s)
    return s


def _lookup_family(canon_soc: str) -> str | None:
    """canonical_soc -> 芯片族键。先精确查族表;否则取族表中 canon_soc 的最长前缀键;
    都无则 None(unknown)。ascend910b 精确命中 ascend910b(不误回退到 ascend910);
    ascend950pr 经前缀回退命中 ascend950。"""
    if not canon_soc:
        return None
    if canon_soc in SOC_FAMILY_TO_PRODUCT_FAMILIES:
        return canon_soc
    # canon_soc 的前缀键(族表 key 是 canon_soc 去子型号尾缀后的族)
    best: str | None = None
    for key in SOC_FAMILY_TO_PRODUCT_FAMILIES:
        if canon_soc.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return best


def match_product_support(
    soc_tokens: list[str],
    product_support: list[str],
) -> tuple[list[str], list[str]]:
    """把 SoC token 列表映射到 constraints.json 的 product_support 产品名。

    返回 (matched, unknown):
      - matched: 命中的 product_support **原字符串**列表(去重保序)。一个 soc_token 命中多个
        产品族时(Ascend910A 同时属推理+训练),每个族在 product_support 里匹配到的项都入列。
      - unknown: 未命中(芯片族不在表 或 product_support 无对应产品)的 soc token 规范化列表
        (去重保序),source-analyst 写入 source_evidence.unknown_socnames 供用户补表。

    匹配规则:候选产品族规范键 是 product_support 项规范化的**子串**(
    "atlasa2" in "atlasa2训练/atlasa2推理" ✓),兼容"/"分隔的复合产品名。
    """
    canon_products = [(p, canonical_product(p)) for p in product_support]
    matched: list[str] = []
    seen_matched: set[str] = set()
    unknown: list[str] = []
    seen_unknown: set[str] = set()
    for tok in soc_tokens:
        canon = canonical_soc(tok)
        if not canon:
            continue
        family = _lookup_family(canon)
        if family is None:
            if canon not in seen_unknown:
                unknown.append(canon)
                seen_unknown.add(canon)
            continue
        candidates = SOC_FAMILY_TO_PRODUCT_FAMILIES.get(family, [])
        hit_any = False
        for product_family_key in candidates:
            for orig, canon_p in canon_products:
                if not canon_p:
                    continue
                if product_family_key in canon_p:
                    if orig not in seen_matched:
                        matched.append(orig)
                        seen_matched.add(orig)
                    hit_any = True
        if not hit_any:
            # 芯片族在表,但 product_support 无对应产品(如源码 SoC 是 Ascend910B 但 product_support
            # 只含 Atlas 350/A3) -> 该 token 不产 patch,归 unknown 提示 source-analyst。
            if canon not in seen_unknown:
                unknown.append(canon)
                seen_unknown.add(canon)
    return matched, unknown
