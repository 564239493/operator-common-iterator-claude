# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
版权信息：华为技术有限公司，版本所有(C) 2025-2025
修改记录：2025/12/25 20:05
功能：定义参数的数据类型生成方法
"""
from typing import Dict

from agent.generators.common_utils.logger_util import get_logger
from agent.generators.data_definition.constants import ParamModelConfig, DataMatchMap


class ParamDtypeModel:
    def __init__(self, operator_name, param_name,
                 dtype_transfer_map: Dict[str, str] = DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP):
        self.logger = get_logger()
        self.operator_name = operator_name
        self.param_name = param_name
        self.dtype_transfer_map = dtype_transfer_map
        self.default_dtype = ParamModelConfig.DEFAULT_PARAM_DTYPE

    def generate_param_dtype(self, data_type):
        """
        根据资料中的数据类型字段生成下游框架代码可以识别的数据类型字段，通过预定义的dtype_transfer_map进行转换
        :param data_type: 资料中的数据类型字段
        :return: 下游代码需要的数据类型字段
        """
        self.logger.debug("Start generate param dtype, operator name: %s, param name: %s", self.operator_name,
                          self.param_name)
        # 大小写不敏感查找：资料 dtype 常为小写 (int32/float16)，而转换表键以大写为主 (INT32)。
        # 直接命中优先，其次按大写归一命中。
        result_dtype = self.dtype_transfer_map.get(data_type)
        if result_dtype is None and isinstance(data_type, str):
            result_dtype = self.dtype_transfer_map.get(data_type.upper())
        if result_dtype is None:
            # 未识别 dtype：透传原始值，禁止静默回退到 DEFAULT_PARAM_DTYPE(fp16)。
            # 旧逻辑对缺键的小写 dtype (如 int32/int64/uint32) 一律回退 fp16，
            # 会把整型张量错误改写成浮点，导致 executor 侧 dtype 不合契约。
            self.logger.warning(
                "Generate param dtype, unrecognized dtype '%s', pass through unchanged. "
                "operator name: %s, param name: %s",
                data_type, self.operator_name, self.param_name)
            result_dtype = data_type
        self.logger.debug(
            f"End generate param dtype, operator name: {self.operator_name}, "
            f"param name: {self.param_name}, dtype: {result_dtype}")
        return result_dtype
