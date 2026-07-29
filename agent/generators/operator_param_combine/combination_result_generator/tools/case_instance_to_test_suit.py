import argparse
import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Any

from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import ParamModelConfig, DataMatchMap
from agent.generators.data_definition.param_models_def import ParamShapeRoleRules, ParamRangeModel
from agent.generators.operator_param_models.case_generate import CaseGenerate
from agent.generators.operator_param_models.param_shape_models import ParamShapeModel
from agent.generators.operator_param_combine.combination_generator_main import PairwiseParamCombinationGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator import GenerationResult, TestSuite, \
    TestCase
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import \
    ParameterAttribute

logger = LazyLogger()


class CaseInstanceToTestSuit:
    def __init__(self, case_instance: List[Dict], case_suit_save_path=None):
        self.case_instance = case_instance
        self.shape_pools, self.shape_strategies = ParamShapeModel.init_shape_model_definition()
        self.global_role_definitions = CaseGenerate.get_global_role_definitions()
        self.case_suit_save_path = case_suit_save_path

    def is_shape_belong_to_strategy(self, shape_value: List[int], strategy_name: str) -> bool:
        """
        判断当前的shape实例是否属于指定的策略
        :shape_value: shape实例
        :strategy_name: 策略名称
        :return: bool
        """
        shape_strategy = self.shape_strategies.get(strategy_name)
        if shape_strategy is not None:
            if shape_strategy.fixed_large_dim in shape_value:
                return True
            if shape_strategy.base_pool is not None:
                base_pool = self.shape_pools.get(shape_strategy.base_pool)
                is_strategy_flag = any(d in base_pool for d in shape_value) if base_pool is not None else False
                if is_strategy_flag:
                    return True
            if shape_strategy.default_pool is not None:
                default_pool = self.shape_pools.get(shape_strategy.default_pool)
                is_strategy_flag = any(d in default_pool for d in shape_value) if default_pool is not None else False
                if is_strategy_flag:
                    return True
        return False

    def transform_shape_to_model(self, shape_value: List[int]) -> str:
        """
        将Tensor的shape的值映射到shape模型上
        :param shape_value: 具体的shape实例
        :return: shape model的名称
        """
        # 如果shape为[]，认为其属于Typical类
        if len(shape_value) == 0:
            return ParamShapeRoleRules.TYPICAL.value

        # 优先级 1: 包含超大维度
        if self.is_shape_belong_to_strategy(shape_value, ParamShapeRoleRules.HAS_LARGE_SIZE.value):
            return ParamShapeRoleRules.HAS_LARGE_SIZE.value

        # 优先级2：包含质数或奇数
        if self.is_shape_belong_to_strategy(shape_value, ParamShapeRoleRules.HAS_ODD_SIZE.value):
            return ParamShapeRoleRules.HAS_ODD_SIZE.value

        # 优先级 3: 包含维度值为 1
        if any(d == 1 for d in shape_value if d > 0):
            return ParamShapeRoleRules.HAS_SIZE_1.value

        # 默认: 典型策略
        return ParamShapeRoleRules.TYPICAL.value

    def get_range_value_thread(self, param_role_model: Dict, model_name: str) -> int | float | None:
        """
        根据指定的模型的名字，获取该模型的数值上下限，仅限static模型
        :param range_value_model: 模型名称
        :param param_role_model: range_value语义角色模型
        :return: 数值阈值
        """
        range_model_data = param_role_model.get(model_name)
        if range_model_data is not None:
            range_model_value = float(range_model_data[0].value)
        else:
            range_model_value = None
        return range_model_value

    def judge_range_value_model_by_numerical(self, range_value: Any, dtype: str, param_role_model: Dict) -> str:
        """
        根据range_value数值的类型，判断模型
        :param range_value: range_value模型
        :param dtype: 数据类型
        :param param_role_model: 数据的语义角色模型
        :return: 模型名称
        """
        sub_normal_value = self.get_range_value_thread(param_role_model, ParamRangeModel.SUBNORMAL.value)
        max_value = self.get_range_value_thread(param_role_model, ParamRangeModel.MAX.value)
        min_value = self.get_range_value_thread(param_role_model, ParamRangeModel.MIN.value)
        if range_value == 0:
            return ParamRangeModel.ZERO.value
        if range_value == 1:
            return ParamRangeModel.ONE.value
        if math.isnan(range_value):
            return ParamRangeModel.NAN.value
        if math.isinf(range_value) and range_value > 0:
            return ParamRangeModel.POSINF.value
        if math.isinf(range_value) and range_value < 0:
            return ParamRangeModel.NEGINF.value
        if sub_normal_value is not None and range_value <= sub_normal_value:
            return ParamRangeModel.SUBNORMAL.value
        if max_value is not None and range_value >= max_value:
            return ParamRangeModel.MAX.value
        if min_value is not None and range_value <= min_value:
            return ParamRangeModel.MIN.value
        if dtype in ParamModelConfig.FLOAT_DTYPE and range_value > 0:
            return ParamRangeModel.POSNORMAL.value
        if dtype in ParamModelConfig.FLOAT_DTYPE and range_value < 0:
            return ParamRangeModel.NEGNORMAL.value
        if dtype in ParamModelConfig.INT_DTYPE and range_value > 0:
            return ParamRangeModel.POS.value
        if dtype in ParamModelConfig.INT_DTYPE and range_value < 0:
            return ParamRangeModel.NEG.value
        return ParamRangeModel.TYPICAL.value

    def transform_range_value_to_model(self, range_value: Any, dtype: str, param_role: str = None):
        """
        将Tensor的range_value的实例映射到具体的range_value的模型上
        :param range_value: 取值范围实例
        :param dtype: 数据类型
        :param param_role: 参数语义角色
        """
        if dtype is None:
            return ParamRangeModel.TYPICAL.value
        if range_value is None:
            return ParamRangeModel.TYPICAL.value
        if param_role is None:
            param_role = ParamModelConfig.DEFAULT_PARAM_ROLE
        param_role_model = self.global_role_definitions.get(param_role)
        if param_role_model is None:
            return ParamRangeModel.TYPICAL.value

        # case1 字符串类型(NaN / Inf / -Inf)
        if isinstance(range_value, str):
            range_value_lower = range_value.lower()
            if range_value_lower in DataMatchMap.ABNORMAL_RANGE_VALUE_MAP:
                return DataMatchMap.ABNORMAL_RANGE_VALUE_MAP.get(range_value_lower)
            return ParamRangeModel.TYPICAL.value

        # case2 布尔类型
        if isinstance(range_value, bool):
            return str(range_value).lower()

        # case3 数值类型，int/float
        if isinstance(range_value, (int, float)):
            return self.judge_range_value_model_by_numerical(range_value, dtype, param_role_model)

        # case4 列表类型（[min, max] 或具体值列表）
        if isinstance(range_value, (list, tuple)):
            if len(range_value) == 0:
                return ParamRangeModel.TYPICAL.value
            if len(range_value) == 1:
                return self.transform_range_value_to_model(range_value[0], dtype, param_role_model)
            if any(math.isnan(v) if isinstance(v, (int, float)) else False for v in range_value):
                return ParamRangeModel.NAN.value
            min_val, max_val = min(range_value), max(range_value)
            if min_val < 0 and dtype in ParamModelConfig.INT_DTYPE:
                return ParamRangeModel.NEG.value
            if max_val > 0 and dtype in ParamModelConfig.INT_DTYPE:
                return ParamRangeModel.POS.value
            if min_val < 0 and dtype in ParamModelConfig.FLOAT_DTYPE:
                return ParamRangeModel.NEGNORMAL.value
            if max_val > 0 and dtype in ParamModelConfig.FLOAT_DTYPE:
                return ParamRangeModel.POSNORMAL.value
            return ParamRangeModel.TYPICAL.value
        return ParamRangeModel.TYPICAL.value

    def transform_case_to_test_suit(self):
        test_suits = TestSuite()
        operator_name = self.case_instance[0].get("name")
        for case in self.case_instance:
            testcase = TestCase()
            case_input = case.get("inputs", [])
            if len(case_input) == 0:
                continue
            testcase.values = defaultdict(dict)
            for input_data in case_input:
                param_name = input_data.get("name")
                param_type = input_data.get("type")
                testcase.values[param_name][ParameterAttribute.IS_PRESENT.value] = True
                dtype_value = input_data.get("dtype")
                if dtype_value is not None:
                    testcase.values[param_name][ParameterAttribute.DTYPE.value] = dtype_value
                length_value = input_data.get("length")
                if length_value is not None:
                    testcase.values[param_name][ParameterAttribute.LENGTH.value] = length_value
                shape_value = input_data.get("shape")
                if shape_value is not None:
                    dimension_value = len(shape_value)
                    testcase.values[param_name][ParameterAttribute.DIMENSION.value] = dimension_value
                    shape_property = self.transform_shape_to_model(shape_value)
                    testcase.values[param_name][ParameterAttribute.SHAPE_PROPERTY.value] = shape_property
                range_value = input_data.get("range_values")
                if range_value is not None:
                    if param_type is not None and param_type in ParamModelConfig.TENSOR_ATK_TYPE:
                        range_value_profile = self.transform_range_value_to_model(range_value, dtype_value)
                    else:
                        range_value_profile = range_value
                    testcase.values[param_name][ParameterAttribute.RANGE_VALUE.value] = range_value_profile
                format_value = input_data.get("format")
                if format_value is not None:
                    testcase.values[param_name][ParameterAttribute.FORMAT.value] = format_value
            test_suits.add(testcase)
        case_generate_result = GenerationResult(suite=test_suits, coverage_rate=0, iterations=0, elapsed_time=0)
        case_data_save_path = os.path.join(self.case_suit_save_path, f"{operator_name}_case_abstract_data.json")
        PairwiseParamCombinationGenerator.save_result(case_generate_result, case_data_save_path)
        return case_generate_result

    @staticmethod
    def load_case_file(case_json_path: str) -> List[Dict] | None:
        if not os.path.exists(case_json_path):
            logger.error(f"Case file {case_json_path} does not exist")
            return None

        with open(case_json_path, "r", encoding="utf-8") as f:
            case_data = json.load(f)
            return case_data

    @staticmethod
    def batch_transform(case_dir: str, abstract_save_path: str) -> None:
        """对目录下所有 {operator_name}.json 用例文件批量转换。

        Args:
            case_dir: 用例 JSON 文件所在目录
            abstract_save_path: 抽象数据保存目录
        """
        if not os.path.isdir(case_dir):
            logger.error(f"Case directory does not exist: {case_dir}")
            return
        os.makedirs(abstract_save_path, exist_ok=True)

        case_files = [
            f for f in os.listdir(case_dir)
            if f.endswith(".json")
               and not f.endswith("_domain_data.json")
               and not f.endswith("_combination_data.json")
               and not f.endswith("_case_abstract_data.json")
        ]

        for file_name in sorted(case_files):
            case_path = os.path.join(case_dir, file_name)
            case_data = CaseInstanceToTestSuit.load_case_file(case_path)
            if case_data is None or (isinstance(case_data, list) and len(case_data) == 0):
                logger.warning(f"Skip empty case file: {file_name}")
                continue
            converter = CaseInstanceToTestSuit(case_instance=case_data,
                                               case_suit_save_path=abstract_save_path)
            converter.transform_case_to_test_suit()


def main():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--cases", help="path to cases.json")
    parser.add_argument("--cases_directory", help="directory with case JSON files")
    parser.add_argument("--case_abstract_save_path", help="output directory")
    args = parser.parse_args()

    from agent.generators.common_utils.logger_util import init_logger
    init_logger(log_name="case_instance_to_test_suit", log_dir="./output/logs")

    if args.cases_directory and args.case_abstract_save_path:
        # 批量模式
        CaseInstanceToTestSuit.batch_transform(
            case_dir=args.cases_directory,
            abstract_save_path=args.case_abstract_save_path,
        )
    elif args.cases and args.case_abstract_save_path:
        # 单文件模式
        case_data = CaseInstanceToTestSuit.load_case_file(args.cases)
        if case_data is None:
            logger.error(f"Case file {args.cases} does not exist")
            return
        converter = CaseInstanceToTestSuit(
            case_instance=case_data,
            case_suit_save_path=args.case_abstract_save_path,
        )
        converter.transform_case_to_test_suit()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
