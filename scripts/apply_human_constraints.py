#!/usr/bin/env python3
"""接入用户编辑的 constraints_copy.json，开启下一轮迭代（确定性）。

人工约束上传通道的「唤醒」侧：监听器检测到 ``<run>/iter_<N>/constraints_copy.json``
被用户上传完成后唤醒会话，会话运行本脚本把用户约束确定性接入为下一轮 iteration 的 ``constraints.json``，
随后由 SKILL 走 CHECK/REPAIR → GENERATE → EXECUTE → GATE → DIAGNOSE。

三阶段（见 docs/WORKFLOW.md §4「人工约束上传通道」）：
- Phase A 预检（不写盘）：run_state 校验、状态门禁、constraints_copy 存在性、target = current_iteration + 1 ≤ max_iterations。
- Phase B 校验（不写盘）：operator_name 一致性 → OperatorRule → validate_constraints → 归一化（内存）→ 复验 → noop 检查。
- Phase C 落盘（按序写盘）：建 iter_%03d → .pre_update 备份 → .submitted 原样存档 → 归一化 constraints.json → 原子更新 run_state → 消费 mv。

依赖 torch：validate_constraints / OperatorRule 经 agent.generators 包导入。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "agent" / "generators" / "common_model_definition.py"

COPY_NAME = "constraints_copy.json"
STATE_NAME = "run_state.json"
CONSTRAINTS_NAME = "constraints.json"

# 唤醒路径允许的状态。
AWAITING_STATE = "AWAITING_HUMAN_CONSTRAINTS"
UPDATE_STATE = "UPDATE_CONSTRAINTS"


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApplyError("CONSTRAINTS_COPY_INVALID_JSON", f"{label} 不是合法 JSON: {exc}")
    if not isinstance(value, dict):
        raise ApplyError("CONSTRAINTS_COPY_INVALID_JSON", f"{label} 必须是 JSON object")
    return value


def _load_operator_rule():
    """照 validate_operator_rule.py 的 importlib 模式加载 OperatorRule，避开 torch 重包 __init__。

    common_model_definition.py 顶层 import torch，故仍需 torch 可用；此处仅避免
    agent/generators/__init__.py 的额外副作用。"""
    spec = importlib.util.spec_from_file_location("operator_rule_contract", MODEL_PATH)
    if spec is None or spec.loader is None:
        raise ApplyError("OPERATOR_RULE_LOAD_FAILED", f"无法加载模型模块: {MODEL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OperatorRule


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _iter_dir(run_dir: Path, n: int) -> Path:
    return run_dir / f"iter_{n:03d}"


def _has_gen_or_exec_artifacts(iter_dir: Path) -> bool:
    """判断该 iter 是否已跑过生成/执行（产物存在即视为已跑）。"""
    for name in ("cases.json", "execution_result.json", "cases_expanded.json"):
        if (iter_dir / name).is_file():
            return True
    return False


class ApplyError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code = code
        self.extra = extra


def _check_preconditions(
        run_dir: Path, max_iterations_override: int | None
) -> tuple[dict[str, Any], int, str]:
    """Phase A：预检（不写盘）。返回 (run_state, target, mode)。

    mode ∈ {"apply", "already_applied"}：
    - apply: AWAITING_HUMAN_CONSTRAINTS，需要正常接入；
    - already_applied: UPDATE_CONSTRAINTS 且目标 iter 无生成/执行产物，幂等放行（接入已完成但轮次未跑的恢复场景）。
    """
    state_path = run_dir / STATE_NAME
    if not state_path.is_file():
        raise ApplyError("RUN_STATE_NOT_FOUND", f"run_state.json 不存在: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApplyError("RUN_STATE_INVALID_JSON", f"run_state.json 不是合法 JSON: {exc}")
    if not isinstance(state, dict):
        raise ApplyError("RUN_STATE_INVALID_JSON", "run_state.json 必须是 JSON object")

    if not state.get("human_constraints_upload"):
        raise ApplyError(
            "UPLOAD_DISABLED",
            "run_state.human_constraints_upload 不为 true；本 run 未启用人工约束上传通道。",
        )

    current = int(state.get("current_iteration", 0) or 0)
    if current < 1:
        raise ApplyError("BAD_CURRENT_ITERATION", f"current_iteration 异常: {current}")

    # 处理 --max-iterations 提升（须 > 当前轮次）。
    if max_iterations_override is not None:
        existing_max = int(state.get("max_iterations", 0) or 0)
        if max_iterations_override <= current:
            raise ApplyError(
                "INVALID_MAX_ITERATIONS",
                f"--max-iterations {max_iterations_override} 须大于当前轮次 {current}。",
            )
        if max_iterations_override < existing_max:
            raise ApplyError(
                "INVALID_MAX_ITERATIONS",
                f"--max-iterations {max_iterations_override} 不得小于已有上限 {existing_max}。",
            )

    raw_state = str(state.get("state", ""))

    if raw_state == AWAITING_STATE:
        target = current + 1
        mode = "apply"
    elif raw_state == UPDATE_STATE:
        # 幂等恢复：UPDATE_CONSTRAINTS 且当前 iter 无生成/执行产物 = 接入已完成但
        # 轮次未跑。此时 current_iteration 已是目标轮，constraints_copy.json 应已
        # 被消费 mv 走——若仍存在则视作重复调用，幂等放行交会话续跑 CHECK/REPAIR。
        cur_iter_dir = _iter_dir(run_dir, current)
        if _has_gen_or_exec_artifacts(cur_iter_dir):
            raise ApplyError(
                "ROUND_ALREADY_RUN",
                f"state={UPDATE_STATE} 但 iter_{current:03d} 已有生成/执行产物；"
                "本轮已跑，不应再次接入。请用 /iterate-operator --resume-run 恢复。",
            )
        target = current
        mode = "already_applied"
    else:
        raise ApplyError(
            "WRONG_STATE",
            f"state={raw_state} 不是可接入状态（{AWAITING_STATE} 或 {UPDATE_STATE}）。"
            "AWAITING 表示等待用户编辑；UPDATE_CONSTRAINTS 仅在接入完成未跑时可幂等放行。",
        )

    effective_max = max_iterations_override or int(state.get("max_iterations", 0) or 0)
    if mode == "apply" and target > effective_max:
        raise ApplyError(
            "MAX_ITERATIONS_REACHED",
            f"target={target} 超过 max_iterations={effective_max}；"
            "可用 --max-iterations N 提升（N 须 > {current}）。",
        )
    return state, target, mode


def _validate_user_constraints(
        user_value: dict[str, Any],
        prev_constraints_path: Path,
        target: int,
) -> tuple[dict[str, Any], int]:
    """Phase B：校验（不写盘）。返回 (normalized_value, normalize_count)。"""
    # 1. operator_name 一致性：用户文件 vs 上一轮 constraints.json。
    if not prev_constraints_path.is_file():
        raise ApplyError(
            "PREV_CONSTRAINTS_NOT_FOUND",
            f"上一轮约束不存在: {prev_constraints_path}",
        )
    prev_value = json.loads(prev_constraints_path.read_text(encoding="utf-8"))
    user_op = str(user_value.get("operator_name", "")).strip()
    prev_op = str(prev_value.get("operator_name", "")).strip()
    if not user_op:
        raise ApplyError("MISSING_OPERATOR_NAME", "用户约束缺 operator_name 字段。")
    if user_op != prev_op:
        raise ApplyError(
            "OPERATOR_NAME_MISMATCH",
            f"operator_name 不一致：用户={user_op}，上一轮={prev_op}。",
            user_operator=user_op,
            prev_operator=prev_op,
        )

    # 2. OperatorRule 校验。
    try:
        OperatorRule = _load_operator_rule()
        OperatorRule(**user_value)
    except ApplyError:
        raise
    except Exception as exc:
        raise ApplyError("OPERATOR_RULE_FAILED", f"OperatorRule 校验失败: {exc}")

    # 3. validate_constraints 完整语义校验（依赖 agent.generators → torch）。
    try:
        from validate_artifacts import validate_constraints
    except ModuleNotFoundError:
        try:
            from scripts.validate_artifacts import validate_constraints  # type: ignore
        except ModuleNotFoundError as exc:
            raise ApplyError("VALIDATE_IMPORT_FAILED", f"无法导入 validate_constraints: {exc}")
    errors = validate_constraints(user_value)
    if errors:
        raise ApplyError(
            "CONSTRAINTS_VALIDATION_FAILED",
            "validate_constraints 报错: " + "; ".join(errors),
            errors=errors,
        )

    # 4. 归一化（内存，照 normalize_constraints.py 的纯函数）。
    try:
        from normalize_constraints import normalize_constraints
    except ModuleNotFoundError:
        try:
            from scripts.normalize_constraints import normalize_constraints  # type: ignore
        except ModuleNotFoundError as exc:
            raise ApplyError("NORMALIZE_IMPORT_FAILED", f"无法导入 normalize_constraints: {exc}")
    normalized = copy.deepcopy(user_value)
    normalize_count = normalize_constraints(normalized)

    # 5. 归一化后复验。
    post_errors = validate_constraints(normalized)
    if post_errors:
        raise ApplyError(
            "POST_NORMALIZE_VALIDATION_FAILED",
            "归一化后复验报错: " + "; ".join(post_errors),
            errors=post_errors,
        )

    # 6. noop 检查：归一化值 vs 上一轮约束（同样归一化后）canonical-JSON 相等。
    prev_normalized = copy.deepcopy(prev_value)
    normalize_constraints(prev_normalized)
    if _canonical(normalized) == _canonical(prev_normalized):
        raise ApplyError(
            "NO_CHANGE",
            "归一化后与上一轮约束完全相同（noop）；请实际修改 constraints_copy.json 后再保存。",
        )
    return normalized, normalize_count


def _atomic_write_state(run_dir: Path, state: dict[str, Any]) -> None:
    state_path = run_dir / STATE_NAME
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)


def _apply(
        run_dir: Path,
        state: dict[str, Any],
        target: int,
        user_raw_bytes: bytes,
        normalized: dict[str, Any],
        max_iterations_override: int | None,
) -> dict[str, Any]:
    """Phase C：落盘（按序写盘）。"""
    now = datetime.now(timezone.utc).isoformat()
    prev_iter_dir = _iter_dir(run_dir, int(state["current_iteration"]))
    target_iter_dir = _iter_dir(run_dir, target)
    prev_constraints = prev_iter_dir / CONSTRAINTS_NAME
    target_constraints = target_iter_dir / CONSTRAINTS_NAME
    pre_update = target_iter_dir / "constraints.json.pre_update"
    submitted = target_iter_dir / "constraints.json.submitted"

    # 防覆盖：目标 iter 已有 constraints.json（非幂等恢复路径）则拒绝。
    if target_constraints.exists() and str(state.get("state")) != UPDATE_STATE:
        raise ApplyError(
            "TARGET_ITER_EXISTS",
            f"{target_constraints} 已存在；目标 iteration 已初始化，拒绝覆盖。",
        )

    # 1. mkdir iter_%03d(target)
    target_iter_dir.mkdir(parents=True, exist_ok=True)

    # 2. .pre_update 备份（沿用 constraint_update_state.py 约定，diff 基线）
    if not pre_update.exists():
        shutil.copy2(prev_constraints, pre_update)

    # 3. 用户原样文件 → .submitted（人工输入存档）
    submitted.write_bytes(user_raw_bytes)

    # 4. 归一化值 → constraints.json（UTF-8、indent 2，与管线序列化一致）
    target_constraints.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 5. 原子更新 run_state.json
    new_state = copy.deepcopy(state)
    new_state["current_iteration"] = target
    new_state["state"] = UPDATE_STATE
    history = new_state.setdefault("history", [])
    history.append({
        "state": UPDATE_STATE,
        "code": "HUMAN_CONSTRAINTS_APPLIED",
        "origin": "human",
        "iteration": target,
        "at": now,
    })
    cc = new_state.get("constraint_check")
    max_rounds = cc.get("max_rounds") if isinstance(cc, dict) else None
    new_state["constraint_check"] = {
        "max_rounds": max_rounds if max_rounds is not None else 3,
        "iteration": target,
        "current_round": 0,
        "status": "pending",
        "report": "",
    }
    if max_iterations_override is not None:
        new_state["max_iterations"] = max_iterations_override
    new_state["updated_at"] = now
    _atomic_write_state(run_dir, new_state)

    # 6. 消费：mv constraints_copy.json → constraints_copy.consumed-<ts>.json
    #    copy_path 位于上一轮（当前 current_iteration）iter 目录，与监听器监听位置一致。
    copy_path = prev_iter_dir / COPY_NAME
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    consumed = prev_iter_dir / f"constraints_copy.consumed-{ts}.json"
    os.replace(copy_path, consumed)

    return {
        "target_iteration": target,
        "target_constraints": str(target_constraints),
        "pre_update": str(pre_update),
        "submitted": str(submitted),
        "consumed": str(consumed),
    }


def run(run_dir: Path, max_iterations_override: int | None) -> dict[str, Any]:
    # Phase A
    state, target, mode = _check_preconditions(run_dir, max_iterations_override)

    current_iter = int(state.get("current_iteration", 0))
    copy_path = run_dir / f"iter_{current_iter:03d}" / COPY_NAME
    if mode == "already_applied":
        # 幂等放行：接入已完成，constraints_copy.json 应已消费。若仍存在（异常重复
        # 调用）也只报告状态，不重复写入。
        return {
            "mode": "already_applied",
            "state": str(state.get("state")),
            "current_iteration": current_iter,
            "message": "接入已完成但本轮未跑，交会话续跑 CHECK/REPAIR。",
        }

    if not copy_path.is_file():
        raise ApplyError(
            "NO_CONSTRAINTS_COPY",
            f"{copy_path} 不存在；请上传用户修改后的约束文件（constraints_copy.json）"
            f"到 iter_{current_iter:03d}/ 目录后再触发。",
        )

    user_raw_bytes = copy_path.read_bytes()
    try:
        user_value = json.loads(user_raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplyError("CONSTRAINTS_COPY_INVALID_JSON", f"constraints_copy.json 解析失败: {exc}")
    if not isinstance(user_value, dict):
        raise ApplyError("CONSTRAINTS_COPY_INVALID_JSON", "constraints_copy.json 必须是 JSON object")

    prev_iter_dir = _iter_dir(run_dir, int(state["current_iteration"]))
    prev_constraints = prev_iter_dir / CONSTRAINTS_NAME

    # Phase B
    normalized, normalize_count = _validate_user_constraints(user_value, prev_constraints, target)

    # Phase C
    payload = _apply(run_dir, state, target, user_raw_bytes, normalized, max_iterations_override)
    payload["mode"] = "applied"
    payload["normalize_count"] = normalize_count
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "接入用户编辑的 constraints_copy.json，开启下一轮迭代（确定性）。"
            "供人工约束上传通道的唤醒侧调用。"
        )
    )
    parser.add_argument("--run-dir", required=True, help="run 目录绝对路径")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="可选；提升 max_iterations 上限（须大于当前轮次）。",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(json.dumps(
            {"ok": False, "code": "RUN_DIR_NOT_FOUND", "run_dir": str(run_dir)},
            ensure_ascii=False,
        ))
        return 2
    try:
        payload = run(run_dir, args.max_iterations)
        print(json.dumps({"ok": True, **payload}, ensure_ascii=False))
        return 0
    except ApplyError as exc:
        print(json.dumps(
            {"ok": False, "code": exc.code, "error": str(exc), **exc.extra},
            ensure_ascii=False,
        ))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
