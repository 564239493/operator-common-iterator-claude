from __future__ import annotations
import copy
import json
import os
from typing import List, Dict, Any, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from agent.generators import OperatorRule, ParamAttributes

from agent.generators.common_utils.data_handle_utils import DataHandleUtil
from agent.generators.common_utils.expression_analysis import ExpressionPreprocessor
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import DataMatchMap, ParamModelConfig
from agent.generators.operator_param_combine.generate_combination_input.combination_constraint_generate import \
    CombinationConstraintGenerate

logger = LazyLogger()


class CombinationInputDataModel(BaseModel):
    dtype: List[str]
    range_value: List[Any]
    is_present: List[bool]
    length: List[int] = []
    dimension: List[int] = []
    shape_property: List[str] = []
    format: List[str] = []


class CombinationInputGenerate:
    def __init__(self, operator_rule_data: OperatorRule, case_num: int = 1, json_save_path: str = None):
        self.operator_rule_data = operator_rule_data
        self.case_num = case_num
        self.choose_dtype_map_combination = None
        self.choose_format_map_combination = None
        self.json_save_path = json_save_path
        self.constraint_generate = CombinationConstraintGenerate(operator_rule_data)

    @staticmethod
    def get_default_range_by_dtype(dtype: str, int_tensor_data_profile, float_tensor_data_profile):
        if dtype not in DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP:
            logger.warning(
                f"Get default range value profile failed, dtype : '{dtype}' is not in dtype map, range model is None")
            return [None]
        dtype_value = DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.get(dtype)
        if dtype_value in ParamModelConfig.FLOAT_DTYPE:
            range_value_profiles = float_tensor_data_profile
        elif dtype_value in ParamModelConfig.INT_DTYPE:
            range_value_profiles = int_tensor_data_profile
        elif dtype_value in ParamModelConfig.BOOL_DTYPE:
            range_value_profiles = ParamModelConfig.BOOL_DATA_PROFILE
        else:
            logger.warning(
                f"Get default range value profile failed, dtype : '{dtype}' is not float / int / bool, range model is None")
            range_value_profiles = [None]
        return range_value_profiles

    @staticmethod
    def get_range_model_by_range(dtype: str, low: int = None, high: int = None):
        """
        如果无法根据allowed_value确定数据range模型，就根据数据类型选择默认模型，如果没有任何一项匹配上，则返回None
        :param dtype: 数据类型
        :param low : 范围值的下界
        :param high: 范围值的上界
        :return: 返回值
        """
        if low is None and high is None:
            int_tensor_data_profile = ParamModelConfig.INT_TENSOR_DATA_PROFILE
            float_tensor_data_profile = ParamModelConfig.FLOAT_TENSOR_DATA_PROFILE
        elif low > 0:
            int_tensor_data_profile = ParamModelConfig.INT_POS_DATA_PROFILE
            float_tensor_data_profile = ParamModelConfig.FLOAT_POS_TENSOR_DATA_PROFILE
        elif high < 0:
            int_tensor_data_profile = ParamModelConfig.INT_NEG_DATA_PROFILE
            float_tensor_data_profile = ParamModelConfig.FLOAT_NEG_TENSOR_DATA_PROFILE
        else:
            int_tensor_data_profile = ParamModelConfig.INT_TENSOR_DATA_PROFILE
            float_tensor_data_profile = ParamModelConfig.FLOAT_TENSOR_DATA_PROFILE
        range_value_profiles = CombinationInputGenerate.get_default_range_by_dtype(dtype, int_tensor_data_profile,
                                                                                   float_tensor_data_profile)
        return range_value_profiles

    def get_param_attribute(self, param_name: str) -> ParamAttributes | None:
        """
        根据参数名称获取参数的属性数据，需要再input以及output中都搜索
        Args:
            param_name: 参数名称
        Returns: 参数属性数据或None，如果input和output中都不存在则返回None
        """
        param_attribute = self.operator_rule_data.inputs.get(param_name)
        if param_attribute is None:
            logger.warning(f"Param : '{param_name}' not in operator input")
            param_attribute = self.operator_rule_data.outputs.get(param_name)
        if param_attribute is None:
            logger.error(f"Param : '{param_name}' not in operator input and output")
        return param_attribute

    def get_length_property(self, param_name: str) -> List[int] | None:
        """
        生成数组参数的length属性
        :param param_name: 参数名称
        :return: 参数的length值
        """
        logger.debug(f"Start generate parameter length, "
                     f"operator name: '{self.operator_rule_data.operator_name}', param name: '{param_name}'")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        length_value, _ = DataHandleUtil.get_relevant_attribute_value(param_name, param_attribute.array_length,
                                                                      "array_length")
        if length_value is None:
            return None
        length_valid_data = set()
        for length in length_value:
            if isinstance(length, list) and len(length) < 2:
                logger.warning(f"Array length data invalid, length should be 2 : '{length}'")
                continue
            if isinstance(length, list):
                # 数组数据的长度，无法枚举的按照最大值，最小值，中间值等价划分
                length_valid_data.add(length[0])
                length_valid_data.add(length[1])
                length_valid_data.add((length[0] + length[1]) // 2)
            elif isinstance(length, int):
                length_valid_data.add(length)
            else:
                logger.warning(f"Array length data invalid, length should be list or int : '{length}'")
        length_valid_data = list(length_valid_data)
        if length_valid_data is None:
            logger.warning(
                f"Generate parameter length, param name : '{param_name}', length value is None, "
                f"use default length: '{ParamModelConfig.DEFAULT_LIST_LENGTH}'")
            return [ParamModelConfig.DEFAULT_LIST_LENGTH]

        logger.debug(f"End generate parameter length, "
                     f"operator name : '{self.operator_rule_data.operator_name}', param name : '{param_name}', "
                     f"length value : '{length_valid_data}'")
        return length_valid_data

    def generate_dimension_property(self, param_name) -> List[int] | None:
        """
        生成参数的shape属性包含的数据全集，用于构建combination，包含shape的维度以及生成其中取值的模型名称：
        Has_Large_Size，Has_Size_1，Has_Odd_Size，Typical
        :return: shape所有属性取值
        """
        logger.debug(
            f"Start generate parameter shape property, "
            f"operator name : '{self.operator_rule_data.operator_name}', param name : '{param_name}'")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        dim_value, _ = DataHandleUtil.get_relevant_attribute_value(param_name, param_attribute.dimensions, "dimensions")
        if dim_value is None:
            dim_count = ParamModelConfig.DEFAULT_TENSOR_SHAPE_SET
        else:
            dim_count = dim_value
        logger.debug(
            f"End generate parameter shape property, operator name : '{self.operator_rule_data.operator_name}', "
            f"param name : '{param_name}', dim count : '{dim_count}'")
        return dim_count

    def generate_shape_property(self, param_name) -> List[str] | None:
        """
        生成参数的shape描述属性，包含shape的维度以及生成其中取值的模型名称：Has_Large_Size，Has_Size_1，Has_Odd_Size，Typical
        :return: shape属性取值，dim_count, dim_value_profile
        """
        logger.debug(
            f"Start generate parameter shape property, "
            f"operator name : '{self.operator_rule_data.operator_name}', param name : '{param_name}'")
        shape_value_profile = ParamModelConfig.DIM_VALUE_PROFILE_LIST
        logger.debug(
            f"End generate parameter shape property, operator name : '{self.operator_rule_data.operator_name}', "
            f"param name : '{param_name}', shape property : '{shape_value_profile}'")
        return shape_value_profile

    def generate_dtype_property(self, param_name: str) -> List[str] | None:
        """
        选择参数的数据类型,如果dtype_map不为空，则在dtype_map中选择一组数据类型作为参数的数据类型，
        否则从parameter_constraints的合法值随机选择
        :param param_name: 参数名称
        :return: 数据类型
        """
        logger.debug(
            f"Start generate dtype property,"
            f"operator name : '{self.operator_rule_data.operator_name}',param name : '{param_name}'")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        dtype_set, _ = DataHandleUtil.get_relevant_attribute_value(param_name, param_attribute.dtype, "dtype")
        if not dtype_set:
            logger.error(
                f"Generate dtype property, param name : '{param_name}', dtype set is empty, use default data dtype")
            return ParamModelConfig.DEFAULT_PARAM_DTYPE_SET
        valid_dtype_set = [each for each in dtype_set if each not in ParamModelConfig.UNSUPPORT_DTYPE]
        # 此处只需要将inputs中的dtype取值作为dtype值域,不能在dyupe_support_map中随机选择一组作为完整的值域
        logger.debug(
            f"End generate dtype property, "
            f"operator name: '{self.operator_rule_data.operator_name}', param name: '{param_name}', dtype: '{valid_dtype_set}'")
        return valid_dtype_set

    def generate_format_property(self, param_name: str) -> List[str] | None:
        """
        选择format,如果不format_support_description为空，则在format_support_description中选择一组数据类型作为参数的format，
        否则从parameter_constraints的合法值随机选择
        :param param_name: 参数名称
        :return: 数据类型
        """
        logger.debug(
            f"Start generate format property,"
            f"operator name : '{self.operator_rule_data.operator_name}',param name : '{param_name}'")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        format_set, _ = DataHandleUtil.get_relevant_attribute_value(param_name, param_attribute.format, "format")
        if not format_set:
            logger.error(
                f"Generate format property, param name : '{param_name}', format set is empty or None")
            return None
        # 此处只需要将inputs中的dtype取值作为dtype值域,不能在dyupe_support_map中随机选择一组作为完整的值域
        logger.debug(
            f"End generate format property, "
            f"operator name: '{self.operator_rule_data.operator_name}', param name: '{param_name}', format: '{format_set}'")
        return format_set

    def generate_range_value_property_by_dtype(self, param_name: str, dtype: str) -> List[
                                                                                         str | int | float | bool] | None:
        """
        生成参数的取值范围属性,检查parameter_constraint.allowed_values和parameter_constraint.not_allowed_values，
        1. 如果合法取值指定的固定取值，则设置为该值，如allowed_values = [0.01]
        2. 如果合法取值指定的是取值范围，则离散化为：[ min_val ], [ max_val ], [ mid_val ], [ near_min_val ],
        [ near_max_val ], Normal. (Also include NaN if the type is float)
        3. 如果未指定任何信息：则离散化为：(Float): PosNormal, NegNormal, Zero, NaN, PosInf, NegInf, SubNormal
        (Integer): Pos, Neg, Zero, Max, Min
        :param param_name: 参数名称
        :param dtype: 数据类型
        :return: 数据取值模型名称或具体值
        """
        logger.debug(f"Start generate param range_value_property, "
                     f"operator name : '{self.operator_rule_data.operator_name}', param name : '{param_name}'...")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        default_data_profile = CombinationInputGenerate.get_range_model_by_range(dtype)
        allowed_values, value_type = DataHandleUtil.get_relevant_attribute_value(param_name,
                                                                                 param_attribute.allowed_range_value,
                                                                                 "allowed_range_value")
        if allowed_values is None or len(allowed_values) == 0:
            dtype_value = DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.get(dtype)
            if dtype_value in ParamModelConfig.STRING_DTYPE:
                default_data_profile = ["None"]
            logger.debug(
                f"Generate range value property, param name : '{param_name}', allowed range value set is None or empty, return default : {default_data_profile}")
            return default_data_profile

        if value_type == "enum":
            logger.debug(
                f"param : '{param_name}', range value type : '{value_type}', range value : '{allowed_values}'")
            return allowed_values

        valid_range_data = []
        for select_allowed_value in allowed_values:
            if isinstance(select_allowed_value, list):
                allowed_value_boundary = DataHandleUtil.get_range_data_boundary(dtype, select_allowed_value)
                if allowed_value_boundary is None:
                    logger.error(
                        f"Operator: '{self.operator_rule_data.operator_name}', param: '{param_name}', "
                        f"dtype : '{dtype}', range value: '{allowed_values}'. solve failed, "
                        f"use default data profile : {default_data_profile}")
                    continue
                low = select_allowed_value[0]
                high = select_allowed_value[1]
                range_value_profile_list = CombinationInputGenerate.get_range_model_by_range(dtype, low, high)
                logger.debug(
                    f"Get data profile by dtype, param name : '{param_name}', range value : '{select_allowed_value}', "
                    f"data profile list: {range_value_profile_list}")
                valid_range_data.extend(range_value_profile_list)
            elif isinstance(select_allowed_value, str) and ExpressionPreprocessor.validate_expression_without_bool(
                    str(select_allowed_value)):
                logger.debug(
                    f"Get data profile by str, param name : '{param_name}', range value : '{select_allowed_value}', "
                    f"data profile list: {select_allowed_value}")
                valid_range_data.append(select_allowed_value)
            elif isinstance(select_allowed_value, (int, float, bool)):
                logger.debug(
                    f"Get data profile by int/float/bool, param name : '{param_name}', range value : '{select_allowed_value}', "
                    f"data profile list: {select_allowed_value}")
                valid_range_data.append(select_allowed_value)
            else:
                logger.error(
                    f"Can't match allowed values, use default function, operator name : '{self.operator_rule_data.operator_name}', "
                    f"param name : '{param_name}', allowed_values : '{allowed_values}'")
        logger.debug(
            f"End generate range value property, operator name : '{self.operator_rule_data.operator_name}', "
            f"param name : '{param_name}', value profile : '{valid_range_data}'")
        return valid_range_data

    def generate_range_value_property(self, param_name, dtype_list):
        """
        选择每个数据类型关联的range_value
        Args:
            param_name: 参数名称
            dtype_list: dtype_list
        Returns: 所有关联的data_profile
        """
        total_range_value_data = []
        for dtype_value in dtype_list:
            range_value_profile = self.generate_range_value_property_by_dtype(param_name, dtype_value)
            if range_value_profile is None:
                continue
            total_range_value_data.extend(range_value_profile)

        if total_range_value_data and self._is_non_tensor_int_symbolic(param_name, total_range_value_data):
            return ParamModelConfig.SHAPE_DIM_VALUES

        seen = set()
        deduped = []
        for item in total_range_value_data:
            key = tuple(item) if isinstance(item, list) else item
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped

    def _is_non_tensor_int_symbolic(self, param_name: str, values: List) -> bool:
        """
        判断参数是不是int类型的非Tensor参数，一般为非算子参数，如BS，K，用于辅助表示Tensor的shape中某个维度
        Args:
            param_name: 参数名
            values: 当前已有的此参数的取值，如果没有range_value，按照默认的值填充：SHAPE_DIM_VALUES

        Returns:

        """
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return False
        param_type_ori, _ = DataHandleUtil.get_relevant_attribute_value(
            param_name, param_attribute.type, "type"
        )
        param_type = DataMatchMap.ACL_TYPE_TRANSFER_ATK_MAP.get(
            param_type_ori, ParamModelConfig.DEFAULT_ATK_TYPE
        )
        if param_type in ParamModelConfig.TENSOR_ATK_TYPE:
            return False
        return all(v in ParamModelConfig.INT_TENSOR_DATA_PROFILE for v in values)

    def generate_is_present_property(self, param_name):
        """
        确定参数是否为必选，如果是必选，则is_present为[True]，否则为[True,False]
        Args:
            param_name: 参数名称
        Returns: List
        """
        logger.debug(
            f"Start generate is present property,"
            f"operator name : '{self.operator_rule_data.operator_name}',param name : '{param_name}'")
        param_attribute = self.get_param_attribute(param_name)
        if param_attribute is None:
            return None
        is_optional_set, _ = DataHandleUtil.get_relevant_attribute_value(param_name, param_attribute.is_optional,
                                                                         "is_optional")
        if is_optional_set is None:
            logger.error(
                f"Generate 'is present' property, param name : '{param_name}', is present set is empty or None")
            return None

        if is_optional_set:
            is_present_set = [True, False]
        else:
            is_present_set = [True]
        logger.debug(
            f"End generate is present property, operator name : '{self.operator_rule_data.operator_name}', "
            f"param name : '{param_name}', is optional : '{is_optional_set}', is present set : '{is_present_set}'")
        return is_present_set

    def generate_combination_input_data(self):
        """
        获取组合覆盖算法需要输入数据
        Returns: dict/json
        """
        combination_input_data = {}
        parameters = {}
        all_param_info = copy.deepcopy(self.operator_rule_data.inputs)
        all_param_info.update(self.operator_rule_data.outputs)
        valid_expr_list = []
        param_type_dict = {}
        for param_name, input_attribute in all_param_info.items():
            param_type_ori, _ = DataHandleUtil.get_relevant_attribute_value(param_name,
                                                                            input_attribute.type, "type")
            param_type = DataMatchMap.ACL_TYPE_TRANSFER_ATK_MAP.get(param_type_ori,
                                                                    ParamModelConfig.DEFAULT_ATK_TYPE)
            param_dtype = self.generate_dtype_property(param_name)
            if param_dtype is None:
                continue
            param_type_dict[param_name] = param_type
            range_value_profile = self.generate_range_value_property(param_name, param_dtype)

            is_range_value_all_none = len(range_value_profile) > 0 and all(x is None for x in range_value_profile)

            # 如果某个参数可取值范围只有None，则该参数不参与组合以及后续用例生成，即生成的用例中没有该参数
            if is_range_value_all_none:
                logger.debug(
                    f"In combination input data generate, operator : '{self.operator_rule_data.operator_name}', "
                    f"param : '{param_name}', range value only has None")
                continue

            is_present = self.generate_is_present_property(param_name)
            if is_present is None:
                continue
            input_data = CombinationInputDataModel(dtype=param_dtype, range_value=range_value_profile,
                                                   is_present=is_present)
            range_value_expr = CombinationConstraintGenerate.get_param_range_value_expr(param_name=param_name,
                                                                                        dtype_list=param_dtype,
                                                                                        range_value_profile=range_value_profile)
            if range_value_expr is not None:
                valid_expr_list.append(range_value_expr)
            if param_type in ParamModelConfig.TENSOR_ATK_TYPE:
                dimension = self.generate_dimension_property(param_name)
                if dimension is not None:
                    input_data.dimension = dimension
                shape_property = self.generate_shape_property(param_name)
                if shape_property is not None:
                    input_data.shape_property = shape_property
                format_property = self.generate_format_property(param_name)
                if format_property is not None:
                    input_data.format = format_property
            if param_type in ParamModelConfig.LIST_ATK_TYPE:
                length_property = self.get_length_property(param_name)
                if length_property is not None:
                    input_data.length = length_property
            parameters[param_name] = input_data.model_dump()
        combination_input_data["parameters"] = parameters
        self.constraint_generate.propagate_constraint_values(parameters, param_type_dict)
        valid_expr_list.extend(
            self.constraint_generate.check_constraint_expr(parameters=parameters, param_type_dict=param_type_dict))
        dtype_support_constraint = self.constraint_generate.solve_dtype_support_description()
        format_support_constraint = self.constraint_generate.solve_format_support_map()
        valid_expr_list.extend(dtype_support_constraint)
        valid_expr_list.extend(format_support_constraint)
        combination_input_data["constraints"] = valid_expr_list
        return combination_input_data

    def save_input_data(self, input_data: Dict):
        if self.json_save_path is None:
            logger.debug("Json_save_path is None, don't save input data to json file")
            return
        if not os.path.exists(self.json_save_path):
            os.makedirs(self.json_save_path)
        save_path = os.path.join(self.json_save_path, self.operator_rule_data.operator_name + ".json")
        input_data_dict = json.dumps(input_data, ensure_ascii=False, indent=4)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(input_data_dict)
        logger.debug(f"Save input data to json file, path : {save_path}")
