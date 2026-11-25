#!/usr/bin/env python3
"""校验 dim_axes 维度轴配置，并可与 constraints.json 交叉核对。

用法：
  python scripts/validate_dim_axes.py --operator aclnnFusedInferAttentionScoreV5 --list-scenes
  python scripts/validate_dim_axes.py --operator aclnnFusedInferAttentionScoreV5 --scene 非量化
  python scripts/validate_dim_axes.py --operator ... --scene ... --constraints runs/<run>/iter_001/constraints.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.generators.operator_param_models.dim_axes import (  # noqa: E402
    DEFAULT_DIM_AXES_PATH,
    DimAxesError,
    load_dim_axes,
    resolve_scene,
)


def _platform_attr(constraints: dict, param_name: str, platform: str | None) -> dict | None:
    for bucket in ("inputs", "outputs"):
        params = constraints.get(bucket)
        if not isinstance(params, dict):
            continue
        attributes = params.get(param_name)
        if attributes is None:
            continue
        if isinstance(attributes, dict):
            if "dimensions" in attributes:
                return attributes
            if platform and isinstance(attributes.get(platform), dict):
                return attributes[platform]
            for value in attributes.values():
                if isinstance(value, dict) and "dimensions" in value:
                    return value
    return None


def _dimension_values(attr: dict) -> list[int]:
    dimensions = attr.get("dimensions") if isinstance(attr, dict) else None
    if isinstance(dimensions, dict):
        values = dimensions.get("value")
    else:
        values = dimensions
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, int) and not isinstance(value, bool)]


def _allowed_values(attr: dict) -> list[Any]:
    allowed = attr.get("allowed_range_value") if isinstance(attr, dict) else None
    if isinstance(allowed, dict):
        values = allowed.get("value")
    else:
        values = allowed
    return values if isinstance(values, list) else []


def cross_check(scene, constraints: dict, platform: str | None) -> list[str]:
    errors: list[str] = []
    for layout_name, orders in scene.layouts.items():
        for tensor_name, tokens in orders.items():
            attr = _platform_attr(constraints, tensor_name, platform)
            if attr is None:
                errors.append(f"constraints 中找不到张量 {tensor_name!r}（布局 {layout_name}）")
                continue
            dims = _dimension_values(attr)
            if dims and len(tokens) not in dims:
                errors.append(
                    f"布局 {layout_name} 的张量 {tensor_name} 秩 {len(tokens)} 不在 dimensions {dims} 中"
                )
    if scene.layout_param:
        attr = _platform_attr(constraints, scene.layout_param, platform)
        if attr is None:
            errors.append(f"constraints 中找不到 layout_param {scene.layout_param!r}")
        else:
            allowed = [value for value in _allowed_values(attr) if isinstance(value, str)]
            if allowed:
                extra = [name for name in scene.layouts if name not in allowed]
                if extra:
                    errors.append(
                        f"布局 {extra} 不在 {scene.layout_param} 的文档枚举 {allowed} 中"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="dim_axes 配置路径")
    parser.add_argument("--operator", default=None)
    parser.add_argument("--scene", default=None)
    parser.add_argument("--constraints", default=None)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--list-scenes", action="store_true")
    args = parser.parse_args()

    config_path = args.config or str(DEFAULT_DIM_AXES_PATH)
    try:
        config = load_dim_axes(config_path, strict=False)
    except DimAxesError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if config is None:
        print(f"FAIL: 配置文件不存在: {config_path}", file=sys.stderr)
        return 2

    errors = list(config.errors)
    profiles = [p for p in config.profiles if not args.operator or p.matches(args.operator)]

    if args.operator:
        profile, scene, reason = resolve_scene(config, args.operator, args.scene)
        if profile is None:
            errors.append(f"算子 {args.operator} 未命中任何 profile")
        if args.list_scenes:
            print(json.dumps({
                "operator": args.operator,
                "profile": profile.id if profile else None,
                "scenes": list(profile.scenes.keys()) if profile else [],
                "resolved_scene": scene.name if scene else None,
                "reason": reason,
            }, ensure_ascii=False, indent=2))
            return 0 if not errors else 2
        if scene is None:
            errors.append(
                f"场景未解析: {reason}；可用场景: "
                f"{list(profile.scenes.keys()) if profile else []}"
            )
        else:
            print(json.dumps({
                "operator": args.operator,
                "profile": profile.id,
                "scene": scene.name,
                "layouts": list(scene.layouts.keys()),
                "required_cases": scene.required_cases(),
                "tensors": scene.tensor_names(),
            }, ensure_ascii=False, indent=2))
            if args.constraints:
                try:
                    constraints = json.loads(Path(args.constraints).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"读取 constraints 失败: {exc}")
                else:
                    errors.extend(cross_check(scene, constraints, args.platform))

    if errors:
        print("FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
