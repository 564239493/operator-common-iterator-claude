from __future__ import annotations
import copy
import json
import os.path
import time
from itertools import islice, cycle
from typing import List, Dict, Any

from typing import TYPE_CHECKING

from agent.generators.operator_param_combine.combination_result_generator.generator import PICTGenerator

if TYPE_CHECKING:
    from agent.generators import OperatorRule

from agent.generators.common_utils.data_handle_utils import DataHandleUtil
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import DataMatchMap, ParamModelConfig
from agent.generators.data_definition.param_models_def import OperatorParameterCombination, ParameterPropertyData, \
    ParameterShapeProperty
from agent.generators.common_utils.timing import track_block, \
    record_time, reset, print_summary
from agent.generators.operator_param_combine.combination_result_generator.engine import build_constraint, \
    build_universe_and_tracker
from agent.generators.operator_param_combine.combination_result_generator.generator import GeneratorOptions, \
    CandidateGenerator, CoverageDrivenGenerator, GenerationResult
from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import \
    ParameterAttribute, ParameterModel
from agent.generators.operator_param_combine.generate_combination_input.combination_input_generate import \
    CombinationInputGenerate

logger = LazyLogger()


class PairwiseParamCombinationGenerator:
    def __init__(self, operator_rule_data: OperatorRule, case_num: int = 1, combination_data_save_path=None):
        self.operator_rule_data = operator_rule_data
        self.case_num = case_num
        self.generated_combinations: List[OperatorParameterCombination] | None = None
        self.combination_data_save_path = combination_data_save_path

    @staticmethod
    def load_config(combination_input_data: Dict) -> GeneratorConfig:
        parameters = {}
        parameter_attributes = [e.value for e in ParameterAttribute]
        for param_name, attrs in combination_input_data["parameters"].items():
            kwargs = {"name": param_name}
            for key, values in attrs.items():
                if key in parameter_attributes:
                    kwargs[key] = tuple(values)
            parameters[param_name] = ParameterModel(**kwargs)

        constraints = tuple(combination_input_data.get("constraints", []))
        return GeneratorConfig(parameters=parameters, constraints=constraints)

    def get_param_combination_input(self) -> tuple[Dict[str, Any], List[OperatorParameterCombination]] | tuple[
        None, None]:
        if self.operator_rule_data is None:
            logger.error(f"Get param combination failed, input operator constraint data is None")
            return None, None

        logger.info(
            f"Start pairwise parameter combination generation, "
            f"operator name: '{self.operator_rule_data.operator_name}'"
        )
        reset()
        t0 = time.perf_counter()
        try:
            with track_block("Parameter info generator"):
                combination_input_generator = CombinationInputGenerate(operator_rule_data=self.operator_rule_data)
                combination_input_data = combination_input_generator.generate_combination_input_data()
                param_domain_data_save_path = os.path.join(self.combination_data_save_path,
                                                           f"{self.operator_rule_data.operator_name}_domain_data.json")
                with open(param_domain_data_save_path, "w", encoding="utf-8") as f:
                    json.dump(combination_input_data, f, indent=2, ensure_ascii=False, default=str)

            logger.debug(
                f"End generate combination input data, operator name : '{self.operator_rule_data.operator_name}'")
            with track_block("Constraint build"):
                generator_config = PairwiseParamCombinationGenerator.load_config(
                    combination_input_data=combination_input_data)
                generator_options = GeneratorOptions()
                constraint_utils = build_constraint(generator_config.constraints)
            logger.debug(f"Constraint build success, operator : '{self.operator_rule_data.operator_name}'")
            with track_block("Universe generator"):
                universe, tracker, builder = build_universe_and_tracker(generator_config, constraint_utils)

            with track_block("Generate testcase suites"):
                candidate_gen = CandidateGenerator(
                    config=generator_config,
                    constraint=constraint_utils,
                    random_seed=generator_options.random_seed,
                    universe=universe,
                    coverage_tracker=tracker,
                )
                gen = PICTGenerator(
                    universe=universe,
                    coverage_tracker=tracker,
                    constraint=constraint_utils,
                    config=generator_options,
                    candidate_generator=candidate_gen,
                    pair_builder=builder,
                    operator_name=self.operator_rule_data.operator_name,
                    domain_data=combination_input_data
                )
                combination_data_result = gen.generate()

            if self.combination_data_save_path is not None:
                combination_data_save_path = os.path.join(self.combination_data_save_path,
                                                          f"{self.operator_rule_data.operator_name}_combination_data.json")
                PairwiseParamCombinationGenerator.save_result(combination_data_result, combination_data_save_path)
                timing_path = os.path.join(self.combination_data_save_path,
                                           f"{self.operator_rule_data.operator_name}_timing.txt")
                record_time("TOTAL", time.perf_counter() - t0)
                with open(timing_path, "w") as f:
                    print_summary(file=f)
            combination_result = self.transformer_to_combination_property(combination_data_result)
        except Exception as e:
            logger.error(
                f"Generator parameter combination failed, combination result is None or empty, "
                f"operator name : '{self.operator_rule_data.operator_name}', err msg : '{str(e)}'")
            return None, None
        return combination_input_data, combination_result

    @staticmethod
    def extract_cases(result: GenerationResult) -> list[dict]:
        cases = []
        for case in result.suite:
            cases.append(case.values)
        return cases

    @staticmethod
    def save_result(result: GenerationResult, output_path: str) -> None:
        cases = PairwiseParamCombinationGenerator.extract_cases(result)
        output = {
            "total_cases": result.suite.size(),
            "coverage_rate": result.coverage_rate,
            "iterations": result.iterations,
            "elapsed_time": result.elapsed_time,
            "cases": cases,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        logger.debug(
            f"Saved '{len(cases)}' case(s) to '{output_path}', pair case num : '{result.suite.size()}', coverage rate : '{result.coverage_rate}'")

    def transformer_to_combination_property(self, combination_data: GenerationResult):
        """
        将组合结果转换为combination_property
        """
        logger.debug(
            f"Start transforming combination property, operator name : '{self.operator_rule_data.operator_name}'")
        param_combination_list = []
        test_suit = combination_data.suite
        all_param_info = copy.deepcopy(self.operator_rule_data.inputs)
        all_param_info.update(self.operator_rule_data.outputs)

        for test_case in test_suit:
            operator_parameter_combination = OperatorParameterCombination(
                operator_name=self.operator_rule_data.operator_name)
            for param_name in test_case.parameters():
                case_attribute = test_case.values.get(param_name, {})
                input_attribute = all_param_info.get(param_name)
                param_type_ori, _ = DataHandleUtil.get_relevant_attribute_value(param_name,
                                                                                input_attribute.type, "type")
                param_type = DataMatchMap.ACL_TYPE_TRANSFER_ATK_MAP.get(param_type_ori,
                                                                        ParamModelConfig.DEFAULT_ATK_TYPE)
                is_operator_param, _ = DataHandleUtil.get_relevant_attribute_value(param_name,
                                                                                   input_attribute.is_operator_param,
                                                                                   "is_operator_param")
                parameter_property_data = ParameterPropertyData(param_name=param_name, param_type=param_type,
                                                                dtype=case_attribute.get(ParameterAttribute.DTYPE),
                                                                format=case_attribute.get(ParameterAttribute.FORMAT),
                                                                range_value_profile=case_attribute.get(
                                                                    ParameterAttribute.RANGE_VALUE),
                                                                length=case_attribute.get(ParameterAttribute.LENGTH),
                                                                is_present=case_attribute.get(
                                                                    ParameterAttribute.IS_PRESENT),
                                                                is_operator_param=is_operator_param)
                if param_type in ParamModelConfig.TENSOR_ATK_TYPE:
                    shape_property = ParameterShapeProperty(dim_count=case_attribute.get(ParameterAttribute.DIMENSION),
                                                            dim_value_profile=case_attribute.get(
                                                                ParameterAttribute.SHAPE_PROPERTY))
                    parameter_property_data.shape_property = shape_property
                operator_parameter_combination.parameter_property.append(parameter_property_data)
            param_combination_list.append(operator_parameter_combination)
        if test_suit.size() < self.case_num:
            param_combination_list = list(islice(cycle(param_combination_list), self.case_num))
        else:
            param_combination_list = param_combination_list[:self.case_num]
        logger.info(
            f"End generate parameter combinations, operator name : '{self.operator_rule_data.operator_name}', pair case num : '{test_suit.size()}', input case num : '{self.case_num}'")
        return param_combination_list
