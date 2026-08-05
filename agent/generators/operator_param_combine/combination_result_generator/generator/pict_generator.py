"""
PICT-compatible generator wrapper.

TODO: 接入真实 pict.exe 外部工具。
当前实现是 CoverageDrivenGenerator 的别名包装，
仅在 candidate_pool_size=100 上与默认值(50)不同。
计划改为:
  1. 构造函数接收 GeneratorConfig + pict.exe 路径
  2. generate() 内部: config → PICT .txt → 调用 pict.exe → 解析 TSV → GenerationResult
"""

from __future__ import annotations

from typing import Optional

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_driven_generator import CoverageDrivenGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult


class PICTGenerator:

    def __init__(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
        constraint: Optional[ConstraintProtocol],
        config: GeneratorOptions,
        candidate_generator: CandidateGenerator,
        pair_builder: PairBuilder,
        *,
        candidate_pool_size: int = 100,
    ) -> None:
        self._inner = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=coverage_tracker,
            constraint=constraint,
            config=config,
            candidate_generator=candidate_generator,
            pair_builder=pair_builder,
            candidate_pool_size=candidate_pool_size,
        )

    def generate(self) -> GenerationResult:
        return self._inner.generate()

    @property
    def algorithm_name(self) -> str:
        return "PICT-compatible"
