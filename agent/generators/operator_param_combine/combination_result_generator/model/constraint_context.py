"""
Constraint Context

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    Runtime variable value context for constraints.
ConstraintContext: 约束求值上下文，提供约束表达式运行时所需要的变量访问环境。
ConstraintContext负责

✅ 保存变量当前值

✅ 根据Variable访问值

✅ 检查变量是否存在

✅ 提供序列化能力

ConstraintContext不负责

❌ AST解析

❌ python表达式执行

❌ 约束优化

❌ Pair覆盖计算
1. 不允许修改Variable定义
VariableModel中x.dtype是静态属性， ConstraintContext中x.dtype=fp16是动态属性，二者分离
2. 如{} -> {"x.dtype" : "fp16"}
3. 高性能查询：禁止ListVar查找，使用dict查找，复杂度O(1)
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from typing import (
    Any,
    Mapping,
)

from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import ContextVariableConflictError, \
    ContextVariableMissingError, ContextValueMissingError


@dataclass(
    slots=True,
)
class ConstraintContext:
    """
    Runtime constraint evaluation context.

    Example:

        {
            "x.dtype": "fp16",
            "weight.dtype": "fp16"
        }

    """

    values: dict[str, Any] = field(
        default_factory=dict
    )

    variable_ids: dict[int, str] = field(
        default_factory=dict
    )

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def set_value(
            self,
            variable_name: str,
            value: Any,
            *,
            overwrite: bool = True,
    ) -> None:

        """
        Set variable value.

        Example:

            x.dtype = fp16

        """
        if (
                not overwrite
                and variable_name in self.values
                and self.values[variable_name] != value
        ):
            raise ContextVariableConflictError(
                f"Variable {variable_name} "
                f"already assigned with "
                f"different value"
            )

        self.values[variable_name] = value

    def set_variable_id(
            self,
            variable_id: int,
            variable_name: str,
    ) -> None:

        """
        Register variable ID mapping.
        """

        self.variable_ids[
            variable_id
        ] = variable_name

    def get_value(
            self,
            variable_name: str,
    ) -> Any:

        """
        Get variable value.
        """

        if variable_name not in self.values:
            raise ContextVariableMissingError(
                f"Variable "
                f"{variable_name}"
                " not found"
            )

        value = self.values[
            variable_name
        ]

        if value is None:
            raise ContextValueMissingError(
                f"Variable "
                f"{variable_name}"
                " has no value"
            )

        return value

    def get_value_by_id(
            self,
            variable_id: int,
    ) -> Any:

        """
        Get value by variable id.
        """

        if variable_id not in self.variable_ids:
            raise ContextVariableMissingError(
                f"Variable id "
                f"{variable_id}"
                " not found"
            )

        return self.get_value(
            self.variable_ids[variable_id]
        )

    def has_value(
            self,
            variable_name: str,
    ) -> bool:

        """
        Check whether variable assigned.
        """

        return variable_name in self.values

    def clear(self) -> None:

        """
        Clear runtime values.
        """

        self.values.clear()

    def snapshot(
            self,
    ) -> dict[str, Any]:

        """
        Return immutable snapshot.

        Used by:
            Cache
            Debug
        """

        return dict(
            self.values
        )

    def update(
            self,
            values: Mapping[str, Any],
    ) -> None:

        """
        Batch update context.
        """

        self.values.update(
            values
        )
