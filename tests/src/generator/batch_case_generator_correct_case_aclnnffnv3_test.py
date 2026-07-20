import json
import unittest
from pathlib import Path
from typing import Dict

from pydantic import TypeAdapter

from agent.generators.atk_common_utils.case_config import CaseConfig
from agent.generators.common_model_definition import OperatorRule
from agent.generators.common_utils.logger_util import LazyLogger, init_logger
from agent.generators.data_definition.param_models_def import ParameterPropertyData
from agent.generators.operator_param_models.batch_case_generate import OperatorCaseGenerator


class BatchCaseGeneratorCorrectCaseAclnnFFNV3Test(unittest.TestCase):
    def testAclnnFFNV3CorrectCase(self):
        operator_case_generate = OperatorCaseGenerator()
        init_logger(log_name="test_correct_case")

        base_case_dir = "tests/resources/case/aclnnFFNV3"
        cases_name = ["aclnnFFNV3-1784101996", "aclnnFFNV3-1784102033"]

        for dir_name in cases_name:
            self.correct_case(operator_case_generate, "%s/%s" % (base_case_dir, dir_name))

    def correct_case(self, operator_case_generate: OperatorCaseGenerator, base_path_dir:str):
        base_path = Path(base_path_dir)
        case_path_str = base_path / "case.json"
        rule_path_str = base_path / "operator_rule_instance.json"
        combos_path_str = base_path / "param_combinations.json"

        case_path = Path(case_path_str)
        rule_path = Path(rule_path_str)
        combos_path = Path(combos_path_str)

        # 1. 加载 CaseConfig
        with open(case_path, 'r', encoding='utf-8') as f:
            case_data = json.load(f)
        case_obj = CaseConfig.model_validate(case_data)

        # 2. 加载 OperatorRule
        with open(rule_path, 'r', encoding='utf-8') as f:
            rule_data = json.load(f)
        rule_obj = OperatorRule.model_validate(rule_data)

        # 3. 加载 param_combinations (Dict[str, ParameterPropertyData])
        with open(combos_path, 'r', encoding='utf-8') as f:
            combos_data = json.load(f)
        # 使用 TypeAdapter 处理字典泛型
        adapter = TypeAdapter(Dict[str, ParameterPropertyData])
        combos_obj = adapter.validate_python(combos_data)

        operator_case_generate.correct_case(case_obj, rule_obj, combos_obj)

if __name__ == '__main__':
    unittest.main()
