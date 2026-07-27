"""
Generator Config

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    Root configuration model.
GeneratorConfig: 系统根配置对象,聚合整个组合生成任务所需要的静态模型
                 test_config.json
                       |
                       |
                 ConfigLoader
                       |
                       v

              GeneratorConfig
                       |
        +--------------+--------------+
        |              |              |
        v              v              v

 ParameterModel   VariableRegistry  ValueRegistry

        |
        |
        v

 Constraint Layer
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

from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import InvalidParameterError, \
    VariableNotFoundError
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel
from agent.generators.operator_param_combine.combination_result_generator.model.value_registry import ValueRegistry
from agent.generators.operator_param_combine.combination_result_generator.model.variable_registry import VariableRegistry


@dataclass(
    frozen=True,
    slots=True,
)
class GeneratorConfig:
    """
    Root model configuration.

    Contains:
        Parameters
        Constraints
        Registries
    """

    parameters: dict[str, ParameterModel]
    constraints: tuple[str, ...] = ()
    variable_registry: VariableRegistry | None = None
    value_registry: ValueRegistry | None = None

    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):

        if not self.parameters:
            raise InvalidParameterError(
                "parameters cannot be empty"
            )

    def get_parameter(self, name: str) -> ParameterModel:

        """
        Get parameter model.
        """

        if name not in self.parameters:
            raise VariableNotFoundError(
                f"Parameter '{name}' not found"
            )

        return self.parameters[name]

    def parameter_names(self) -> tuple[str, ...]:

        """
        Return parameter names.
        """

        return tuple(self.parameters.keys())

    def validate(self) -> None:

        """
        Validate configuration model.

        Detailed validation belongs
        to ConfigLoader.
        """

        for name, parameter in self.parameters.items():
            if name != parameter.name:
                raise InvalidParameterError(
                    f"Parameter key '{name}' != model name '{parameter.name}'"
                )

    def with_registries(self, variable_registry: VariableRegistry, value_registry: ValueRegistry) -> "GeneratorConfig":

        """
        Create new config with registries.

        Since config is frozen,
        return a new instance.
        """

        return GeneratorConfig(
            parameters=self.parameters,
            constraints=self.constraints,
            variable_registry=variable_registry,
            value_registry=value_registry,
            metadata=self.metadata,
        )
