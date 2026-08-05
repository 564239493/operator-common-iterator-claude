
"""
Variable Model

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    Define atomic combination variable.
    
VariableModel: Coverage层最小工作单元，一个可以参与组合生成的最小变量单元。
ParameterModel
        |
        | expand
        ↓
VariableModel
        |
        | consumed by
        ↓
Coverage / Constraint / Generator

输入：
{
  "x": {
    "dtype": [
      "float16",
      "float32"
    ],
    "dimension": [
      2,
      3
    ]
  }
}
展开后：
ParameterModel

x

    |
    |
    +---- x.dtype
    |
    +---- x.dimension
产生：
VariableModel(
    variable_id=0,
    parameter_name="x",
    attribute_name="dtype",
    values=("float16","float32")
)
不保存业务逻辑，只是数据结构，所以，禁止以下操作：
variable.is_valid()
variable.generate()
variable.cover()
不允许修改：
因为后续的Pair ID计算、Coverage Hash、Constraint Cache都依赖稳定对象，因此：
@dataclass(frozen=True)
高性能：
使用：slots=True，减少内存、属性查找开销

"""


from __future__ import annotations


from dataclasses import dataclass, field
from typing import (
    Any,
    Mapping,
    Tuple,
)


from .exceptions import InvalidValueError,InvalidVariableError, InvalidVariableIdError



@dataclass(
    frozen=True,
    slots=True,
)
class VariableModel:
    """
    Atomic variable model.

    Example:

        x.dtype

        weight.dimension

    """

    variable_id: int

    parameter_name: str

    attribute_name: str

    values: Tuple[Any, ...]

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )


    def __post_init__(self) -> None:
        """
        Validate immutable model.
        """

        if self.variable_id < 0:
            raise InvalidVariableIdError(
                "variable_id must be >= 0"
            )


        if not self.parameter_name:
            raise InvalidVariableError(
                "parameter_name cannot be empty"
            )


        if not self.attribute_name:
            raise InvalidVariableError(
                "attribute_name cannot be empty"
            )


        if not self.values:
            raise InvalidValueError(
                f"{self.full_name} has empty domain"
            )



    @property
    def full_name(self) -> str:
        """
        Full variable name.

        Example:

            x.dtype
        """

        return (
            f"{self.parameter_name}."
            f"{self.attribute_name}"
        )



    @property
    def domain_size(self) -> int:
        """
        Number of possible values.
        """

        return len(self.values)



    def contains(
        self,
        value: Any,
    ) -> bool:
        """
        Check whether value exists.

        """

        return value in self.values



    def index_of(
        self,
        value: Any,
    ) -> int:
        """
        Return value index.

        Used by:
            Coverage mapping
            Pair encoding

        """

        try:
            return self.values.index(value)

        except ValueError:

            raise InvalidValueError(
                f"{value} not found "
                f"in {self.full_name}"
            )



    def to_dict(self) -> dict[str, Any]:
        """
        Serialize model.

        """

        return {
            "variable_id":
                self.variable_id,

            "parameter_name":
                self.parameter_name,

            "attribute_name":
                self.attribute_name,

            "full_name":
                self.full_name,

            "values":
                list(self.values),

            "metadata":
                dict(self.metadata),
        }