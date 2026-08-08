#!/usr/bin/env python3
"""Build the ACLNN base prompt by mechanically splitting the main v4 prompt.

The v4 file remains an immutable historical source.  This builder removes only
the sections registered below and replaces the duplicated Pydantic listing with
the repository model contract.  Extracted sections live under
``knowledge/aclnn`` and are assembled by ``select_prompt.py``.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "prompts" / "operator_constraints_extract_v4.md"
DEFAULT_OUTPUT = ROOT / "prompts" / "operator_constraints" / "base.md"

EXTRACTED_HEADINGS = {
    "## 3. 顶层 JSON Schema（Pydantic）",
    "##### `dimensions.value` 解析表（来自 `knowledge/dimensions/SKILL.md`）",
    "##### `allowed_range_value` 文本描述 → 结构化映射（来自 `knowledge/allowed_range/SKILL.md`）",
    "##### aclIntArray 特殊取值（`knowledge/allowed_range/examples/acl_int_array.md`）",
    "##### bool 类型参数（`no_constraint.md` / v3 加强）",
    "##### 无约束参数处理（`no_constraint.md` / v3 加强）",
    "##### D+. `shape_value_dependency` 必须按 §4.6.5 B.1 隐式 bool 门控分支（v3 合并 v4 增补）",
    "##### H. 条件维数 / dimNum 门控（支持场景表，v4 增补，通用规则）",
    "#### 4.6.4 隐式参数（命名维度变量 / 外部常量）识别",
    "## 5. 平台与 dtype 命名规范",
    "## 6. 表达式编写规范",
    "## 7. `expr_type` 取值字典",
    "## 附录：知识库路径速查表",
}

SCHEMA_REPLACEMENT = """## 3. 输出对象合同（唯一事实源）

不得在提示词中复制或重声明 Pydantic schema。输出必须严格使用
`agent/generators/common_model_definition.py` 中当前版本的完整
`OperatorRule` 对象结构，包括全部嵌套对象、必填字段和 `extra=\"forbid\"` 规则。

生成 JSON 后必须实际执行：

```text
python scripts/validate_operator_rule.py <constraints.json>
python scripts/normalize_constraints.py <constraints.json>
python scripts/validate_artifacts.py constraints <constraints.json>
```

只有三条命令均成功，且 `OperatorRule` 校验不抛异常，结果才有效；不得仅凭模型
自检宣称结构正确。校验失败时必须修正 JSON 后重跑，不能跳过或降级。
"""

DIRECTORY_REPLACEMENT = """## 0. 使用与装配顺序

本文件是由远程 `main` 的 `operator_constraints_extract_v4.md` 机械拆分得到的 ACLNN
基础提示词，只保留稳定任务、字段提取流程、边界处理和质量门禁。运行时按以下顺序，
步骤 1-5 由 `scripts/select_prompt.py` 在 run 初始化（PLAN）阶段完成并冻结为
`prompt_v1.md` + `prompt_preanalysis.json` + `prompt_assembly.json`（含 sha256），
extractor 只执行步骤 6（读冻结快照提取 JSON 并实际校验）：

1. 完整阅读当前算子文档并完成结构预分析（`route_aclnn_knowledge.preanalyze_document`）；
2. 加载 ACLNN 默认基础知识与通用知识（manifest `default_load` 模块）；
3. 依据当前文档信号加载特征知识，并按算子名精确加载单算子知识（`triggers` / `operator_name_eq`）；
4. 逐模块做适用性判断（含 `reject_on` 负向否决），以当前文档为最高事实源；
5. 冻结 `base + applicable knowledge` 快照和组装记录后再提取（`select_prompt.assemble`）；
6. 生成 JSON，执行规范化与 `OperatorRule` 实际校验。

本文件中指向已拆分到知识库的小节的章节引用，均改写为「`knowledge/aclnn/<file>.md`
§<标题>」的文件+标题定位，不再依赖本文件内的章节编号。ACLNN 与 torch_npu 使用
完全独立的提示词和知识根，禁止跨 family 引用。
"""

# Ordered (regex, replacement) pairs. More specific / multi-token patterns run
# before their shorter prefixes so they win (e.g. §4.6.5 B.1 before §4.6.5,
# §6.3 模式 6.1 before §6.3 模式 N before bare §6.3). Each numeric section ref
# carries a (?!\d) guard so §4.6.5 never matches §4.6.55-style ids. Replacements
# omit a leading verb so they read naturally after 已有的「见 / 按 / 来自 / 参考」.
REFERENCE_REWRITES: list[tuple[str, str]] = [
    # §4.6.5 B.1 -> operator module (most specific first)
    (r"§4\.6\.5(?!\d)\s*B\.1", r"`knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1"),
    (r"§4\.6\.5(?!\d)", r"`knowledge/aclnn/features/nz_matmul.md` §4.6.5"),
    # §4.6.3 H / D+ -> operator modules (G / I stay in base, untouched)
    (r"§4\.6\.3(?!\d)\s*H", r"`knowledge/aclnn/operators/grouped_matmul_v5.md` §4.6.3 H"),
    (r"§4\.6\.3(?!\d)\s*D\+", r"`knowledge/aclnn/operators/batch_matmul_weight_nz.md` §D+"),
    # §4.6.3 migrated sub-sections -> common modules (dimensions / allowed_range
    # tables were extracted to knowledge modules; aclDataType / aclIntArray-dtype /
    # TensorList-length / 逐字一致性 rules stay in base and keep §4.6.3 self-refs)
    (r"§4\.6\.3(?!\d)\s*dimensions 解析表", r"`knowledge/aclnn/common/dimensions.md` §解析表"),
    (r"§4\.6\.3(?!\d)\s*allowed_range 文本→结构化映射", r"`knowledge/aclnn/common/allowed_range.md` §映射表"),
    (r"§4\.6\.3(?!\d)\s*aclIntArray 特殊取值", r"`knowledge/aclnn/common/allowed_range.md` §aclIntArray特殊取值"),
    # §4.6.10 A / B(.x) / bare -> broadcast feature
    (r"§4\.6\.10(?!\d)\s*A", r"`knowledge/aclnn/features/broadcast.md` §A"),
    (r"§4\.6\.10(?!\d)\s*B(?:\.\d+)?", r"`knowledge/aclnn/features/broadcast.md` §B"),
    (r"§4\.6\.10(?!\d)", r"`knowledge/aclnn/features/broadcast.md` §4.6.10"),
    # §4.6.6 -> backward_partial
    (r"§4\.6\.6(?!\d)", r"`knowledge/aclnn/features/backward_partial.md` §4.6.6"),
    # §4.6.7 / §4.6.8 / §4.6.11 / §4.6.12 -> format_cast
    (r"§4\.6\.(?:7|8|11|12)(?!\d)(?:\s*[A-Z](?:\.\d+)?)?",
     r"`knowledge/aclnn/features/format_cast.md` §4.6.7"),
    # §4.6.9 -> implicit_pos
    (r"§4\.6\.9(?!\d)(?:\s*[A-Z](?:\.\d+)?)?",
     r"`knowledge/aclnn/features/implicit_pos.md` §4.6.9"),
    # §4.6.4 -> implicit_parameters
    (r"§4\.6\.4(?!\d)", r"`knowledge/aclnn/common/implicit_parameters.md` §隐式参数"),
    # §5.x -> platform_dtype (pair phrase first, then individuals)
    (r"§5\.2(?!\d)\s*/\s*§5\.3(?!\d)", r"`knowledge/aclnn/common/platform_dtype.md` §dtype / §format"),
    (r"§5\.3(?!\d)", r"`knowledge/aclnn/common/platform_dtype.md` §format"),
    (r"§5\.2(?!\d)", r"`knowledge/aclnn/common/platform_dtype.md` §dtype"),
    (r"§5\.1(?!\d)", r"`knowledge/aclnn/common/platform_dtype.md` §平台"),
    # §6.3 模式 N / bare / §6.1 -> expression_language
    (r"§6\.3(?!\d)\s*模式\s*6\.1", r"`knowledge/aclnn/common/expression_language.md` §常用模式 模式 6.1"),
    (r"§6\.3(?!\d)\s*模式\s*(\d+(?:\.\d+)?)", r"`knowledge/aclnn/common/expression_language.md` §常用模式 模式 \1"),
    (r"§6\.3(?!\d)", r"`knowledge/aclnn/common/expression_language.md` §常用模式"),
    (r"§6\.1(?!\d)", r"`knowledge/aclnn/common/expression_language.md` §语法"),
    # §7 -> expr_type dictionary
    (r"§7(?!\d)", r"`knowledge/aclnn/common/expression_language.md` §expr_type"),
]


def _heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+", line)
    return len(match.group(1)) if match else None


def _drop_block(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return text
    finish = text.find(end, begin + len(start))
    if finish < 0:
        raise ValueError(f"cannot find end marker {end!r} after {start!r}")
    return text[:begin] + text[finish:]


def split_prompt(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "## 0. 目录结构":
            output.extend(DIRECTORY_REPLACEMENT.splitlines())
            index += 1
            while index < len(lines) and not lines[index].startswith("## 1."):
                index += 1
            continue
        if line in EXTRACTED_HEADINGS:
            level = _heading_level(line)
            if line.startswith("## 3."):
                output.extend(SCHEMA_REPLACEMENT.splitlines())
            index += 1
            in_fence = False
            while index < len(lines):
                candidate = lines[index]
                if candidate.lstrip().startswith("```"):
                    in_fence = not in_fence
                    index += 1
                    continue
                next_level = None if in_fence else _heading_level(candidate)
                if next_level is not None and next_level <= level:
                    break
                index += 1
            continue
        output.append(line)
        index += 1

    rendered = "\n".join(output).rstrip() + "\n"
    rendered = rendered.replace("结构与第 3 章 schema 完全一致", "结构与第 3 章引用的项目模型完全一致")
    rendered = rendered.replace("字段名、字段类型、层级结构必须与第 3 章 schema", "字段名、字段类型、层级结构必须与第 3 章引用的项目模型")
    # Remove remaining exact-operator gates from general examples/self-checks.
    # Their complete first-version rules are now loaded only from operators/*.
    rendered = re.sub(
        r"(?ms)^> 本节来自 `aclnnGroupedMatmulV5` 闭环：.*?^> 点明转置的两种编码语义，避免误抽。\n",
        "> 文档中的“转置”可能由 stride/数据排布表达，也可能由 shape 元组重排表达；先判定编码语义，再选择约束形式。\n",
        rendered,
    )
    rendered = re.sub(r"(?m)^- 典型算子 `aclnnBatchMatMulWeightNz`.*\n", "", rendered)
    rendered = re.sub(
        r"(?ms)(12\. \*\*MatMul Reduce 维度相等必须落库.*?必须按实际布局落为 `shape_value_dependency`。)\n    对 `aclnnBatchMatMulWeightNz`：.*?(?=^13\.)",
        r"\1\n",
        rendered,
    )
    rendered = re.sub(
        r"(?ms)^23\. \*\*`aclnnBatchMatMulWeightNz` 转置隐式变量完整性.*?(?=^24\.)",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?ms)^31\. \*\*支持场景表 → 维数联动自检.*?(?=^32\. \*\*必选参数)",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?m)^    唯一顺序特例：`aclnnBatchMatMulWeightNz`.*\n    `mat2_transposed`.*\n",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?ms)^    f\. \*\*shape_value_dependency 门控完整性\*\*:.*?(?=^19\.)",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?m)^(    e\. 若出现 \"Reduce 维度需要与 \.\.\. 相等\"，必须存在真实 Reduce 轴相等约束)；对于\n       `aclnnBatchMatMulWeightNz`.*\n       分支并引用两者。",
        r"\1。",
        rendered,
    )
    rendered = _drop_block(
        rendered,
        "    f. **shape_value_dependency 门控完整性**：",
        "19. **TensorList 长度关系完整性**",
    )
    rendered = _drop_block(
        rendered,
        "23. **`aclnnBatchMatMulWeightNz` 转置隐式变量完整性**",
        "24. **format↔rank 完整性**",
    ) if "23. **`aclnnBatchMatMulWeightNz`" in rendered else rendered
    rendered = _drop_block(
        rendered,
        "31. **支持场景表 → 维数联动自检",
        "32. **必选参数\"只支持 nullptr\"取值语义自检",
    ) if "31. **支持场景表 → 维数联动自检" in rendered else rendered
    # Rewrite dangling section refs to their new knowledge-module home
    # (file + title), so no pointer stays dead after the split.
    for pattern, repl in REFERENCE_REWRITES:
        rendered = re.sub(pattern, repl, rendered)
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Build split ACLNN base prompt from v4.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    rendered = split_prompt(source.read_text(encoding="utf-8"))
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated ACLNN base prompt: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
