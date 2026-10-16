import ast
import glob
import json
import os
import logging
from typing import List

import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.pict_generator import PICTGenerator, \
    _guarded_cross_eq_lines, _translate_or
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult

from agent.generators.operator_param_combine.combination_result_generator.engine import (
    build_constraint,
    build_universe_and_tracker,
    load_config,
)
from agent.generators.common_utils.logger_util import init_logger

from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel

_OUTPUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "output")
)
_TEST_DATA_OUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "test_data_out")
)


def create_config():
    x = ParameterModel(name="x", dtype=("fp16", "fp32"), dimension=(2, 4))
    weight = ParameterModel(name="weight", dtype=("int8", "int16"))
    return GeneratorConfig(parameters={"x": x, "weight": weight})


def build_components(config: GeneratorConfig):
    factor_values = []
    for param in config.parameters.values():
        for attr_name, domain in param.attributes().items():
            if domain:
                for val in domain:
                    factor_values.append(
                        FactorValue(Factor(param.name, attr_name), val)
                    )
    builder = PairBuilder()
    all_pairs = builder.build(factor_values)
    universe = PairUniverse(all_pairs)
    tracker = CoverageTracker(universe)
    return universe, tracker, builder


class TestPICTGenerator:

    def test_init(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        assert gen is not None

    def test_algorithm_name(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        assert gen.algorithm_name == "PICT-compatible"

    def test_generate_returns_result(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result is not None
        assert isinstance(result, GenerationResult)

    def test_generate_with_custom_pool_size(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            candidate_pool_size=20,
        )
        result = gen.generate()
        assert result is not None

    def test_generate_coverage_increases(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        initial_rate = tracker.coverage_rate()
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100, target_coverage=1.0)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate >= initial_rate


def _discover_domain_data_files():
    pattern = os.path.join(_OUTPUT_DIR, "*_domain_data.json")
    files = sorted(glob.glob(pattern))
    result = []
    for path in files:
        filename = os.path.basename(path)
        operator_name = filename[: -len("_domain_data.json")]
        result.append((filename, operator_name, path))
    return result


_DOMAIN_DATA_FILES = _discover_domain_data_files()


def _operator_name_from_filename(filename):
    return filename[: -len("_domain_data.json")]


def _save_combination_data(result: GenerationResult, operator_name: str) -> str:
    cases = [case.values for case in result.suite]
    output = {
        "total_cases": result.suite.size(),
        "coverage_rate": result.coverage_rate,
        "iterations": result.iterations,
        "elapsed_time": result.elapsed_time,
        "cases": cases,
    }
    os.makedirs(_TEST_DATA_OUT_DIR, exist_ok=True)
    output_path = os.path.join(_TEST_DATA_OUT_DIR, "{}_combination_data.json".format(operator_name))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    return output_path

@pytest.fixture(autouse=True)
def _init_logger_fixture(tmp_path):
    """LazyLogger 全局惰性初始化：未 init 时首次打日志抛 RuntimeError。

    参照 tests/src/generator/batch_case_generator_correct_case_aclnnffnv3_test.py
    的惯例先 init_logger，日志落到 pytest 临时目录，避免污染仓库。
    """
    init_logger(
        log_name="test_pict_generator",
        log_dir=str(tmp_path),
        log_level=logging.WARNING,
        console_output=False,
    )


class TestPICTGeneratorDomainData:
    """以 output 目录下每个 {operator_name}_domain_data.json 为输入，
    验证 PICTGenerator 能正常生成 combination_data，并落盘到 test_data_out。
    """

    @pytest.mark.parametrize(
        "filename,operator_name,path",
        _DOMAIN_DATA_FILES,
        ids=[op for _, op, _ in _DOMAIN_DATA_FILES],
    )
    def test_domain_data_generate_combination_data(self, filename, operator_name, path):
        with open(path, encoding="utf-8") as f:
            domain_data = json.load(f)

        parameters = domain_data.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            pytest.skip("empty domain data, nothing to combine")

        config = load_config(path)
        constraint = build_constraint(config.constraints)
        universe, tracker, builder = build_universe_and_tracker(config, constraint)

        options = GeneratorOptions()
        candidate_gen = CandidateGenerator(
            config=config,
            constraint=constraint,
            random_seed=options.random_seed,
            universe=universe,
            coverage_tracker=tracker,
        )

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=constraint,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            domain_data=domain_data,
            operator_name=operator_name,
        )

        result = gen.generate()

        assert isinstance(result, GenerationResult)
        assert result.suite is not None
        assert result.suite.size() > 0

        output_path = _save_combination_data(result, operator_name)
        assert os.path.isfile(output_path)


class TestSavePictModel:
    """验证 PICTGenerator.save_pict_model 落盘行为。

    说明：_build_pict_model() 是纯函数，不设置 self._pict_model（该赋值在
    _build_with_real_pict 中完成）；测试用 _attach_model 辅助方法镜像生产
    赋值逻辑后再验证落盘。
    """

    @staticmethod
    def _make_generator(model_output_dir):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = PICTGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            operator_name="TestOp",
            model_output_dir=model_output_dir,
        )
        return gen

    @staticmethod
    def _attach_model(gen):
        """镜像 _build_with_real_pict 中对模型的两行生产赋值。"""
        model = gen._build_pict_model()
        assert model is not None
        gen._pict_model = model
        gen._pict_col_map = model.col_map
        return model

    def test_save_pict_model_without_dir_returns_none(self):
        gen = self._make_generator(model_output_dir=None)
        self._attach_model(gen)
        assert gen.save_pict_model() is None

    def test_save_pict_model_without_model_returns_none(self, tmp_path):
        gen = self._make_generator(model_output_dir=str(tmp_path))
        assert gen._pict_model is None
        assert gen.save_pict_model() is None

    def test_save_pict_model_writes_file(self, tmp_path):
        gen = self._make_generator(model_output_dir=str(tmp_path))
        model = self._attach_model(gen)

        model_path = gen.save_pict_model()

        assert model_path is not None
        assert os.path.isfile(model_path)
        assert os.path.basename(model_path) == "TestOp_pict_model.txt"
        with open(model_path, encoding="utf-8") as f:
            assert f.read() == model.model_text


class TestTranslateOrGuardedCrossEq:
    """覆盖 _translate_or 的互斥 OR 守卫式跨参等式展开。"""

    def _node(self, expr: str) -> ast.AST:
        """把表达式解析为顶层 AST 节点。

        :param expr: 约束表达式原文
        :return: 顶层 AST 节点（本例均为 BoolOp）
        """
        return ast.parse(expr, mode="eval").body

    def test_mutually_exclusive_or_emits_guarded_lines(self) -> None:
        """互斥 OR 且析取项含跨参等式时，生成带钉值守卫的 IF-THEN 行。

        示例：(groupType.range_value == -1 and x.length == weight.length)
              or (groupType.range_value == 0)
              -> IF ([groupType_range_value] = -1) AND [x_length] = 128 THEN [weight_length] = 128;
        """
        node = self._node(
            "(groupType.range_value == -1 and x.length == weight.length) or (groupType.range_value == 0)"
        )
        columns = {"groupType_range_value", "x_length", "weight_length"}
        parameters = {"groupType_range_value": [-1, 0], "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        lines = _guarded_cross_eq_lines(node, columns, warnings, parameters)
        assert lines == [
            "IF ([groupType_range_value] = -1) AND [x_length] = 128 THEN [weight_length] = 128;"
        ]

    def test_non_exclusive_or_emits_nothing(self) -> None:
        """析取项之间不互斥（没有同列不同常量）时，不生成任何守卫行（保守）。"""
        node = self._node("(a.range_value == 1 and x.length == weight.length) or (b.range_value == 2)")
        columns = {"a_range_value", "b_range_value", "x_length", "weight_length"}
        parameters = {"a_range_value": [1], "b_range_value": [2], "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        assert _guarded_cross_eq_lines(node, columns, warnings, parameters) == []

    def test_in_clause_pins_expand_into_guard(self) -> None:
        """M-14：``in [...]`` 钉值展开进守卫——按列分组、笛卡尔积、每组合一条守卫行。

        示例：groupType.range_value == -1 and splitItem.range_value in [0, 1]
              and x.length == weight.length
              -> si=0 与 si=1 各一条（守卫更窄、保留原式的 splitItem 条件）。
        """
        node = self._node(
            "(groupType.range_value == -1 and splitItem.range_value in [0, 1]"
            " and x.length == weight.length)"
            " or (groupType.range_value == 0)"
        )
        columns = {"groupType_range_value", "splitItem_range_value", "x_length", "weight_length"}
        parameters = {"groupType_range_value": [-1, 0], "splitItem_range_value": [0, 1],
                      "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        lines = _guarded_cross_eq_lines(node, columns, warnings, parameters)
        assert lines == [
            "IF ([groupType_range_value] = -1) AND ([splitItem_range_value] = 0)"
            " AND [x_length] = 128 THEN [weight_length] = 128;",
            "IF ([groupType_range_value] = -1) AND ([splitItem_range_value] = 1)"
            " AND [x_length] = 128 THEN [weight_length] = 128;",
        ]

    def test_in_clause_without_in_unchanged(self) -> None:
        """M-14 回归核验：不含 ``in`` 的输入上，输出与 M-12 现行为逐字一致（零回归面）。

        示例：(groupType.range_value == -1 and x.length == weight.length)
              or (groupType.range_value == 0)
              -> IF ([groupType_range_value] = -1) AND [x_length] = 128 THEN [weight_length] = 128;
        """
        node = self._node(
            "(groupType.range_value == -1 and x.length == weight.length)"
            " or (groupType.range_value == 0)"
        )
        columns = {"groupType_range_value", "x_length", "weight_length"}
        parameters = {"groupType_range_value": [-1, 0], "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        lines = _guarded_cross_eq_lines(node, columns, warnings, parameters)
        assert lines == [
            "IF ([groupType_range_value] = -1) AND [x_length] = 128 THEN [weight_length] = 128;"
        ]

    def test_in_clause_guard_combo_over_limit_falls_back(self) -> None:
        """M-14 边界：守卫组合数（笛卡尔积）超上限 24 时回退现行行为并记录 warning。

        示例：两个 5 值 ``in``（5×5=25 > 24）
              -> 0 行守卫 + warnings 含 "guard combination count 25 exceeds limit"。
        """
        node = self._node(
            "(groupType.range_value == -1 and splitItem.range_value in [0, 1, 2, 3, 4]"
            " and groupListType.range_value in [0, 1, 2, 3, 4] and x.length == weight.length)"
            " or (groupType.range_value == 0)"
        )
        columns = {"groupType_range_value", "splitItem_range_value",
                   "groupListType_range_value", "x_length", "weight_length"}
        parameters = {"groupType_range_value": [-1, 0], "splitItem_range_value": [0, 1, 2, 3, 4],
                      "groupListType_range_value": [0, 1, 2, 3, 4],
                      "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        lines = _guarded_cross_eq_lines(node, columns, warnings, parameters)
        assert lines == []
        assert any("exceeds limit" in w for w in warnings)

    def test_translate_or_appends_guarded_lines(self) -> None:
        """_translate_or 在既有翻译结果之上追加守卫行，且既有 OR 行保持不变。"""
        node = self._node(
            "(groupType.range_value == -1 and x.length == weight.length) or (groupType.range_value == 0)"
        )
        columns = {"groupType_range_value", "x_length", "weight_length"}
        parameters = {"groupType_range_value": [-1, 0], "x_length": [128], "weight_length": [128]}
        warnings: List[str] = []
        result = _translate_or(node, columns, warnings, parameters)
        assert result is not None
        assert result[-1] == (
            "IF ([groupType_range_value] = -1) AND [x_length] = 128 THEN [weight_length] = 128;"
        )
        assert any(line.startswith("(") and " OR " in line for line in result)


