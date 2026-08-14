#!/usr/bin/env python3
"""Build the torch_npu base prompt by mechanically splitting the v3 prompt.

Mirrors ``scripts/build_aclnn_prompt_base.py`` for the torch_npu family. The v3
file remains an immutable historical source; this builder removes only §3
(``固定输出数据结构``) and replaces the duplicated listing with the repository
model contract. The remaining sections (§1/§2/§4-§8) are kept verbatim. Output
lives at ``prompts/torch_npu_constraints/base.md`` and is assembled by
``select_torch_npu_prompt.py``.
"""
from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "prompts" / "torch_npu_constraints_extract_v3.md"
DEFAULT_OUTPUT = ROOT / "prompts" / "torch_npu_constraints" / "base.md"

SCHEMA_REPLACEMENT = """## 3. 输出对象合同（唯一事实源）

不得在提示词中复制或重声明 Pydantic schema。输出必须严格使用
`agent/generators/common_model_definition.py` 中当前版本的完整
`OperatorRule` 对象结构，包括全部嵌套对象、必填字段和 `extra="forbid"` 规则。
torch_npu 与 ACLNN 共享同一 `OperatorRule` JSON 契约，但提示词与知识根相互
隔离，禁止跨 family 引用。

生成 JSON 后必须实际执行：

```text
python scripts/validate_operator_rule.py <constraints.json>
python scripts/normalize_constraints.py <constraints.json>
python scripts/validate_artifacts.py constraints <constraints.json>
```

只有三条命令均成功，且 `OperatorRule` 校验不抛异常，结果才有效；不得仅凭模型
自检宣称结构正确。校验失败时必须修正 JSON 后重跑，不能跳过或降级。
"""


def split_prompt(text: str) -> str:
    # Replace only the duplicated JSON schema listing under §3 with the
    # model-contract block; keep §3.1 (ValueWithSrcText rules) and §3.2
    # (platform nesting, enforced by PLATFORM_CONTRACT_MARKERS) verbatim.
    begin = text.find("## 3. 固定输出数据结构")
    if begin < 0:
        return text.rstrip() + "\n"
    fence_start = text.find("```json", begin)
    if fence_start < 0:
        raise SystemExit("cannot find §3 schema json fence in torch_npu v3 source")
    fence_end = text.find("```", fence_start + 7)
    if fence_end < 0:
        raise SystemExit("cannot find §3 schema json fence close in torch_npu v3 source")
    line_end = text.find("\n", fence_end)
    end = (line_end + 1) if line_end >= 0 else len(text)
    rendered = text[:begin] + SCHEMA_REPLACEMENT + "\n" + text[end:]
    return rendered.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build split torch_npu base prompt from v3.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    rendered = split_prompt(source.read_text(encoding="utf-8"))
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated torch_npu base prompt: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
