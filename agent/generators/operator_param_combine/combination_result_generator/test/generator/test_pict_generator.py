import glob
import json
import os
import logging

import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.pict_generator import PICTGenerator
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


