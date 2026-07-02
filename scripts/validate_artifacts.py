#!/usr/bin/env python3
"""Validate stage artifact structure without invoking an LLM."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_constraints(value) -> list[str]:
    if not isinstance(value, dict):
        return ["constraints must be an object"]
    try:
        from agent.generators.common_model_definition import OperatorRule

        OperatorRule(**value)
        return []
    except Exception as exc:
        return [f"OperatorRule validation failed: {exc}"]


def validate_cases(value) -> list[str]:
    if not isinstance(value, list):
        return ["cases must be an array"]
    if not value:
        return ["cases must not be empty"]
    return [f"cases[{index}] must be an object" for index, item in enumerate(value) if not isinstance(item, dict)]


def validate_execution(value) -> list[str]:
    if not isinstance(value, dict):
        return ["execution result must be an object"]
    errors = []
    required = ("status", "mode", "passed", "failed", "total", "records", "engine_error")
    errors.extend(f"missing field: {key}" for key in required if key not in value)
    passed, failed, total = value.get("passed", 0), value.get("failed", 0), value.get("total", 0)
    if all(isinstance(item, int) for item in (passed, failed, total)) and passed + failed != total:
        errors.append("passed + failed must equal total")
    if not isinstance(value.get("records", []), list):
        errors.append("records must be an array")
    return errors


def validate_analysis(value) -> list[str]:
    if not isinstance(value, dict):
        return ["analysis must be an object"]
    allowed = {"constraint_extraction", "generator_bug", "executor_bug"}
    return [] if value.get("root_cause") in allowed else ["invalid root_cause"]


# CPU golden 推导 (atc-cpu-golden-derivation skill) 完成后, cases_executor.py
# 里 generator.py 写入的 dummy 块必须被替换. 这里的标记 / dummy 函数若仍存在,
# 说明推导未真正执行或未生效, real 模式上传的会是 torch.ones 假参考, 精度比对
# 无意义. 该校验是质量门禁兜住 "dummy 上线" 的确定性依据.
_EXECUTOR_DUMMY_MARKERS = (
    "_dummy_output",
    "# [FALLBACK]",
    "# TODO: CPU_GOLDEN",
)


def validate_executor(path: str) -> list[str]:
    file_path = Path(path)
    if not file_path.is_file():
        return [f"executor file not found: {path}"]
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read executor file: {exc}"]
    errors: list[str] = []
    hits = [m for m in _EXECUTOR_DUMMY_MARKERS if m in source]
    if hits:
        errors.append(
            "CPU golden 推导未完成, 仍含 dummy 标记: "
            + ", ".join(hits)
            + " — 需先跑 atc-cpu-golden-derivation skill 替换后再执行 real"
        )
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"executor 语法错误 (推导输出可能残缺): {exc}")
    return errors


VALIDATORS = {
    "constraints": validate_constraints,
    "cases": validate_cases,
    "execution": validate_execution,
    "analysis": validate_analysis,
    "executor": validate_executor,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=VALIDATORS)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        # executor 校验对象是 Python 源文件, 直接传路径; 其余校验对象是
        # JSON 产物, 先解析再传结构.
        if args.kind == "executor":
            errors = VALIDATORS[args.kind](args.path)
        else:
            errors = VALIDATORS[args.kind](load(args.path))
    except Exception as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
