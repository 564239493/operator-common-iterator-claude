"""Public facade: ``TestCaseGenerator``.

该 facade 取代了原先的随机采样 mock 实现，改为直接调用
``agent.generators.operator_handle_main.single_operator_handle``（来自
``operator_case_generator`` 的正式算子用例生成代码）。

设计目标
--------

* **稳定公共 API**：节点 / 路由 / MCP 工具仍按
  ``TestCaseGenerator(json_constraints, seed=seed).generate(...)`` 调用，
  返回 ``list[CaseConfig]``（即 ``single_operator_handle`` 的原始输出），
  不再做任何 ``GeneratorContext`` / ``TestCaseRecord`` 中间层转换。
* **正式生成逻辑**：

  - 直接把原始的 ``json_constraints`` dict（即 ``document_versions.json_constraints``
    字段从 MCP / DB 取出来的 Python 字典对象）透传给 ``single_operator_handle``，
    走完整的 inter-parameter 约束求解 + Z3 solver 流程。
    ``single_operator_handle`` 内部已经会用 ``OperatorRule(**dict(operator_constraint))``
    做 Pydantic 校验，调用方无需再做 ``GeneratorContext`` 中间层转换。
  - 提供两个生成入口：
    * ``generate(count)`` —— 跨所有 ``product_support`` 平台生成，返回扁平
      ``list[CaseConfig]``（按平台顺序拼接），适合直接落盘为单个 JSON 文件。
    * ``generate_by_platform(count)`` —— 返回 ``dict[platform, list[CaseConfig]]``，
      适合按平台分文件落盘。
  - 平台列表缺失时回退到默认平台（``ATLAS_A3_TRAIN_AND_INFER_SERIES``）。

注意
----

* 真正的种子行为由下游 ``CaseGenerate`` + ``ParamCombinationGenerator`` 控制（随机选择
  dtype、shape、range 等），不再由 facade 显式注入 ``random.seed``。``seed`` 参数保留
  以保持 API 兼容 —— 下游内部的 random 调用使用 Python 全局 ``random`` 状态时，
  调用方可在外部 ``random.seed(seed)`` 来获得确定行为。
* 正式生成日志按算子和平台拆分到
  ``logs/generate_case_<operator>_<platform>.log``，不同平台不共用文件。
* 入参 dict 中的 ``constraints_in_parameters`` 字典会一并传入，Z3 求解器
  据此修正 / 求解参数。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from agent.generators.atk_common_utils.case_config import CaseConfig
from agent.generators.common_utils.data_handle_utils import DataHandleUtil
from agent.generators.data_definition.param_models_def import RunPlatform
from agent.generators.operator_handle_main import single_operator_handle

logger = logging.getLogger(__name__)


def _safe_log_component(value: str, fallback: str) -> str:
    """Return a filesystem-safe, stable component for generator log names."""
    text = value.strip() if isinstance(value, str) else ""
    # 平台通常是枚举字符串，但同时兼容 ``a/b``、空格及 Windows 非法文件名字符。
    sanitized = re.sub(r"[^0-9A-Za-z_.-]+", "_", text).strip("._-")
    return sanitized or fallback


def _platform_log_name(operator_name: str, platform: str) -> str:
    """Build ``generate_case_<operator>_<platform>`` without an extension."""
    operator_part = _safe_log_component(operator_name, "unknown_operator")
    platform_part = _safe_log_component(platform, "unknown_platform")
    return f"generate_case_{operator_part}_{platform_part}"


DEFAULT_COUNT = 10
DEFAULT_SEED: int | None = 42

# 默认平台与 operator-agent 的 ``product_support`` 命名空间对应。
# 若传入的 ``json_constraints`` 没有 ``product_support`` 字段，则使用此默认平台。
_DEFAULT_PLATFORM = RunPlatform.ATLAS_A3_TRAIN_AND_INFER_SERIES.value


def _extract_operator_name(json_constraints: dict[str, Any]) -> str:
    """从原始 ``json_constraints`` 中读取算子名，缺失时返回空串。"""
    name = json_constraints.get("operator_name")
    return str(name) if isinstance(name, str) else ""


def _extract_supported_platforms(json_constraints: dict[str, Any]) -> list[str]:
    """从原始 ``json_constraints`` 中读取平台列表；为空时回退到默认平台。

    兼容历史 / 新格式两种字段名：
    * ``product_support`` —— 新格式（``assemble_result`` 写出的 result.json）
    * ``supported_platforms`` —— 早期格式
    """
    raw = json_constraints.get("product_support")
    if raw is None:
        raw = json_constraints.get("supported_platforms")
    platforms = [str(p) for p in (raw or []) if isinstance(p, str) and p]
    return platforms


class TestCaseGenerator:
    """基于正式生成逻辑的 ``TestCaseGenerator`` 入口。

    与旧的 mock 实现相比，该类不再使用 ``case_builder`` / ``shape_sampler`` 等
    随机采样模块，也不再做任何 ``GeneratorContext`` / ``TestCaseRecord`` 中间
    转换 —— ``json_constraints`` dict 直接透传给 ``single_operator_handle``，返回的
    也是 ``CaseConfig``（即 ``single_operator_handle`` 的原始输出）。

    ``generate(count)`` / ``generate_by_platform(count)`` 行为：

    1. 从入参 dict 中读取 ``operator_name`` 与 ``product_support``；
    2. 对每个平台调用 ``single_operator_handle`` 生成 ``count`` 个用例；
    3. 把结果以 ``list[CaseConfig]``（扁平）或 ``dict[platform, list[CaseConfig]]``
       （按平台）形式返回，**不再做任何字段重映射 / 包装**。

    ``count`` 是 **per-platform** 的目标数，因此 ``generate`` 返回的列表长度约为
    ``count * len(supported_platforms)``。
    """

    __test__ = False  # not a pytest test class

    def __init__(
        self,
        json_constraints: dict[str, Any],
        *,
        seed: int | None = DEFAULT_SEED,
    ) -> None:
        if not isinstance(json_constraints, dict):
            raise TypeError(
                f"json_constraints must be a dict, got {type(json_constraints).__name__}"
            )
        self._constraints = json_constraints
        self._operator_name = _extract_operator_name(json_constraints)
        self._supported_platforms = _extract_supported_platforms(json_constraints)
        self._seed = seed
        logger.debug(
            "TestCaseGenerator: operator=%s count_default=%d seed=%s platforms=%d",
            self._operator_name, DEFAULT_COUNT, seed, len(self._supported_platforms),
        )

    @property
    def operator_name(self) -> str:
        """算子名（来自入参 dict 的 ``operator_name`` 字段）。"""
        return self._operator_name

    @property
    def supported_platforms(self) -> list[str]:
        """入参 dict 中声明的 ``product_support`` 列表。"""
        return list(self._supported_platforms)

    def _resolve_platforms(self) -> list[str]:
        platforms = list(self._supported_platforms or [_DEFAULT_PLATFORM])
        return platforms or [_DEFAULT_PLATFORM]

    def _apply_seed(self) -> None:
        """在调用下游 ``random.choice`` 之前注入 seed，最大化确定性。"""
        if self._seed is None:
            return
        try:
            import random
            random.seed(self._seed)
        except Exception as seed_err:  # pragma: no cover - 防呆
            logger.debug("Failed to apply random seed %s: %s", self._seed, seed_err)

    def generate_for_platform(
        self,
        platform: str,
        count: int = DEFAULT_COUNT,
        jsonl_save_path: str | None = None,
        json_save_path: str | None = None,
    ) -> list[CaseConfig]:
        """针对单个 ``platform`` 调用 ``single_operator_handle``，返回原始 ``CaseConfig`` 列表。

        指定 ``jsonl_save_path`` 时，生成过程逐条写入 JSONL；调用结束（包括异常
        或中断）后立即转换为正式 JSON。``json_save_path`` 默认与 JSONL 目录相同。
        """
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count}")
        if count == 0:
            return []

        # 正式生成代码使用全局 LazyLogger。每个平台在独立上下文中临时切换 logger，
        # 避免同一算子的所有平台继续混写 generate_case_<operator>.log。
        from agent.generators.common_utils.logger_util import DocumentLogContext

        log_name = _platform_log_name(self._operator_name, platform)
        cases: list[CaseConfig] = []
        with DocumentLogContext(log_name, log_dir="./logs") as platform_logger:
            self._apply_seed()
            platform_logger.info(
                "Start platform generation, operator=%s, platform=%s, count=%d",
                self._operator_name,
                platform,
                count,
            )
            try:
                cases = single_operator_handle(
                    operator_constraint=self._constraints,
                    platform=platform,
                    case_num=count,
                    jsonl_save_path=jsonl_save_path,
                )
            except Exception as gen_err:
                platform_logger.exception(
                    "single_operator_handle failed for platform=%s, operator=%s: %s",
                    platform, self._operator_name, gen_err,
                )
            finally:
                if jsonl_save_path is not None:
                    DataHandleUtil.convert_jsonl_to_json(
                        api_name=self._operator_name,
                        jsonl_save_path=jsonl_save_path,
                        json_save_path=json_save_path or jsonl_save_path,
                    )
            platform_logger.info(
                "End platform generation, operator=%s, platform=%s, generated=%d",
                self._operator_name,
                platform,
                len(cases or []),
            )
        return list(cases or [])

    def generate_by_platform(
        self,
        count: int = DEFAULT_COUNT,
        jsonl_save_path: str | None = None,
    ) -> dict[str, list[CaseConfig]]:
        """按平台分组生成用例，返回 ``dict[platform, list[CaseConfig]]``。

        当 ``json_constraints`` 没有 ``product_support`` 字段时，会回退到默认平台。
        指定 ``jsonl_save_path`` 时，每个平台先写入独立子目录，随后由
        ``generate_for_platform`` 转换为 JSON，避免同名算子产物相互覆盖。
        """
        platforms = self._resolve_platforms()
        result: dict[str, list[CaseConfig]] = {}
        for platform in platforms:
            platform_jsonl_path = None
            if jsonl_save_path is not None:
                platform_jsonl_path = os.path.join(
                    jsonl_save_path, platform.replace("/", "_")
                )
            result[platform] = self.generate_for_platform(
                platform,
                count,
                jsonl_save_path=platform_jsonl_path,
            )
        logger.info(
            "TestCaseGenerator: operator=%s platforms=%d per_platform=%d total=%d",
            self._operator_name,
            len(platforms),
            count,
            sum(len(v) for v in result.values()),
        )
        return result

    def generate(
        self,
        count: int = DEFAULT_COUNT,
        jsonl_save_path: str | None = None,
    ) -> list[CaseConfig]:
        """跨所有平台生成用例，返回扁平 ``list[CaseConfig]``（按平台顺序拼接）。"""
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count}")
        if count == 0:
            return []

        platforms = self._resolve_platforms()
        all_cases: list[CaseConfig] = []
        for platform in platforms:
            platform_jsonl_path = None
            if jsonl_save_path is not None:
                platform_jsonl_path = os.path.join(
                    jsonl_save_path, platform.replace("/", "_")
                )
            all_cases.extend(
                self.generate_for_platform(
                    platform,
                    count,
                    jsonl_save_path=platform_jsonl_path,
                )
            )
        logger.info(
            "TestCaseGenerator: operator=%s platforms=%d per_platform=%d total=%d",
            self._operator_name, len(platforms), count, len(all_cases),
        )
        return all_cases


# Re-export the formal entry point for callers that prefer it directly.
__all__ = [
    "DEFAULT_COUNT",
    "DEFAULT_SEED",
    "TestCaseGenerator",
    "single_operator_handle",
]
