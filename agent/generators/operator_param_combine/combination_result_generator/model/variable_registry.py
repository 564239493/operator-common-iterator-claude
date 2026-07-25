

"""
Variable Registry

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    VariableRegistry : VariableName ↔ VariableID:管理所有参与组合生成的 VariableModel，并提供 VariableName / VariableID / VariableModel 的双向访问
ParameterModel
    |
    | expand_variables()

    ↓
VariableModel[]
    |
    |
    ↓
VariableRegistry
    |
    |
    +----------------+
    |                |
 VariableID      VariableName

与ValueRegistry的关系：
VariableRegistry管理变量名的映射，如
VariableID
0 ---> x.dtype
1 ---> x.dimension
2 ---> weight.dtype
ValueRegistry：管理变量取值的映射，如：
ValueID
0 ---> fp16
1 ---> fp32
2 ---> int8
最终Coverage使用：
VariableID + ValueID
(variable_id=0,value_id=1) 表示 x.dtype = fp32
设计原则：VariableId全局唯一，Variable不可修改，注册顺序允许变化，即不保证x.dtype永远为0
"""


from __future__ import annotations


from dataclasses import (
    dataclass,
    field,
)

from typing import (
    Mapping,
    Any,
)

from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import DuplicateVariableError, \
    InvalidVariableIdError, VariableNotFoundError
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel
from agent.generators.operator_param_combine.combination_result_generator.model.variable_model import VariableModel


@dataclass(slots=True)
class VariableRegistry:
    """
    Registry for VariableModel.

    Example:

        0 -> x.dtype

        1 -> x.dimension

    """


    variables_by_id: dict[int, VariableModel] = field(
        default_factory=dict
    )


    variables_by_name: dict[str, int] = field(
        default_factory=dict
    )


    next_id: int = 0


    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )



    def register(
        self,
        variable: VariableModel,
    ) -> int:

        """
        Register one variable.
        """


        name = variable.full_name


        if name in self.variables_by_name:

            raise DuplicateVariableError(
                f"Variable {name} already exists"
            )


        variable_id = self.next_id


        # 注意：
        # Registry重新分配ID
        # 不直接使用VariableModel中的ID

        registered_variable = VariableModel(

            variable_id=variable_id,

            parameter_name=variable.parameter_name,

            attribute_name=variable.attribute_name,

            values=variable.values,

            metadata=variable.metadata,

        )


        self.variables_by_id[
            variable_id
        ] = registered_variable


        self.variables_by_name[
            name
        ] = variable_id


        self.next_id += 1


        return variable_id



    def register_parameter(
        self,
        parameter: ParameterModel,
    ) -> list[int]:

        """
        Expand ParameterModel
        and register variables.

        """

        variables = parameter.expand_variables()


        return [

            self.register(variable)

            for variable in variables

        ]



    def get_by_id(
        self,
        variable_id:int,
    ) -> VariableModel:

        """
        Get variable by ID.
        """


        if variable_id not in self.variables_by_id:

            raise InvalidVariableIdError(
                f"Variable ID '{variable_id}' not found"
            )

        return self.variables_by_id[
            variable_id
        ]



    def get_by_name(
        self,
        name:str,
    ) -> VariableModel:

        """
        Get variable by full name.

        Example:

            x.dtype
        """


        if name not in self.variables_by_name:

            raise VariableNotFoundError(
                f"Variable '{name}' not found"
            )


        return self.get_by_id(

            self.variables_by_name[name]

        )



    def contains(
        self,
        name:str,
    ) -> bool:

        """
        Check variable existence.
        """

        return name in self.variables_by_name



    def all_variables(
        self,
    ) -> list[VariableModel]:

        """
        Return all variables.
        """

        return list(
            self.variables_by_id.values()
        )



    def size(
        self,
    ) -> int:

        """
        Number of variables.
        """

        return len(
            self.variables_by_id
        )



    def clear(
        self,
    ) -> None:

        """
        Clear registry.
        """

        self.variables_by_id.clear()

        self.variables_by_name.clear()

        self.next_id = 0



    def snapshot(
        self,
    ) -> dict[int, dict]:

        """
        Export registry snapshot.
        """

        return {

            vid:
            variable.to_dict()

            for vid, variable

            in self.variables_by_id.items()

        }



