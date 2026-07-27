"""
Parameter Model

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    Strong typed parameter definition.

ParameterModel : 强类型参数模型，一个组合测试参数对象及其所有属性域。
Config输入
    ↓
ParameterModel
    ↓
VariableModel
    ↓
Coverage / Constraint / Generator

输入：
{
    "x": {
        "dtype": [
            "float16",
            "float32"
        ],
        "range_value": [
            "Positive",
            "Negative"
        ],
        "dimension": [
            2,
            3
        ]
    }
}

ParameterModel：

name=x

dtype
 ├── float16
 └── float32


range_value
 ├── Positive
 └── Negative


dimension
 ├──2
 └──3

VariableModel：

x.dtype

x.range_value

x.dimension
"""

from __future__ import annotations

from dataclasses import dataclass, field

from enum import Enum

from typing import (
    Any,
    Mapping,
)

from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import EmptyParameterNameError, \
    EmptyAttributeDomainError, ParameterAttributeError
from agent.generators.operator_param_combine.combination_result_generator.model.variable_model import VariableModel


class ParameterAttribute(str, Enum):
    """
    Parameter supported attributes.
    """

    DTYPE = "dtype"

    RANGE_VALUE = "range_value"

    IS_PRESENT = "is_present"

    LENGTH = "length"

    DIMENSION = "dimension"

    SHAPE_PROPERTY = "shape_property"

    FORMAT = "format"


PARAMETER_ATTRIBUTES = (

    ParameterAttribute.DTYPE,

    ParameterAttribute.RANGE_VALUE,

    ParameterAttribute.IS_PRESENT,

    ParameterAttribute.LENGTH,

    ParameterAttribute.DIMENSION,

    ParameterAttribute.SHAPE_PROPERTY,

    ParameterAttribute.FORMAT,
)


@dataclass(
    slots=True,
)
class ParameterModel:
    """
    Strong typed parameter model.
    """

    name: str

    dtype: tuple[str, ...] = ()

    range_value: tuple[Any, ...] = ()

    is_present: tuple[bool, ...] = ()

    length: tuple[int, ...] = ()

    dimension: tuple[int, ...] = ()

    shape_property: tuple[str, ...] = ()

    format: tuple[str, ...] = ()

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self):

        if not self.name:
            raise EmptyParameterNameError(
                "Parameter name cannot be empty"
            )

        self._validate_domains()

    def _validate_domains(self):
        """
        Validate attribute values.

        None means configuration error.

        Empty tuple means attribute absent
        """

        for attribute in PARAMETER_ATTRIBUTES:
            value = getattr(self,attribute.value)

            if value is None:
                raise EmptyAttributeDomainError(
                    f"{self.name}.{attribute}"
                    " domain is empty"
                )

    def get_attribute_domain(self,attribute: ParameterAttribute) -> tuple[Any, ...]:

        """
        Get attribute domain.
        """
        if attribute not in PARAMETER_ATTRIBUTES:
            raise ParameterAttributeError(
                f"Parameter attribute is invalid : {attribute}"
            )

        return getattr(self,attribute.value)

    def expand_variables(self,start_id: int = 0) -> list[VariableModel]:

        """
        Expand ParameterModel
        into VariableModel list.
        """
        variables = []
        variable_id = start_id
        for attribute in PARAMETER_ATTRIBUTES:
            values = getattr(self,attribute.value)
            if values:
                variables.append(
                    VariableModel(
                        variable_id=variable_id,
                        parameter_name=self.name,
                        attribute_name=attribute.value,
                        values=tuple(values),
                    )
                )
                variable_id += 1

        return variables

    def attributes(self) -> dict[str, tuple[Any, ...]]:

        """
        Export all attributes.
        """

        return {attribute.value: getattr(self, attribute.value) for attribute in PARAMETER_ATTRIBUTES}
