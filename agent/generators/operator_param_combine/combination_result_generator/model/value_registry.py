"""
Value Registry

Architecture:
    V1.2

Module:
    M1 Model Layer

Description:
    Global value and value-id mapping.

ValueRegistry: 负责Domain Value 与内部 ValueID 双向映射的组件：Value  <----------------> ValueID
如：
输入：
x.dtype = [
    "float16",
    "float32",
    "int8"
]
注册后：
ValueID    Value

0          float16

1          float32

2          int8

ParameterModel
      |
      |
      v
VariableModel
      |
      |
      v
VariableRegistry
      |
      |
      v
ValueRegistry
      |
      |
      v
Coverage Pair Encoder
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

from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import InvalidValueError, ValueIdNotFoundError


@dataclass(
    slots=True,
)
class ValueRegistry:
    """
    Global Value Registry.

    Example:

        float16 -> 0

        float32 -> 1

    """

    value_to_id: dict[Any, int] = field(
        default_factory=dict
    )

    id_to_value: dict[int, Any] = field(
        default_factory=dict
    )

    next_id: int = 0

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def register(self, value: Any) -> int:

        """
        Register value.

        Existing value returns existing ID.
        """
        if value is None:
            raise InvalidValueError(
                "Value cannot be None"
            )

        if value in self.value_to_id:
            return self.value_to_id[value]

        value_id = self.next_id

        self.value_to_id[value] = value_id

        self.id_to_value[value_id] = value

        self.next_id += 1

        return value_id

    def register_batch(
            self,
            values: list[Any],
    ) -> list[int]:

        """
        Register multiple values.
        """

        return [

            self.register(value)

            for value in values

        ]

    def get_id(
            self,
            value: Any,
    ) -> int:

        """
        Get ValueID.
        """

        if value not in self.value_to_id:
            raise InvalidValueError(
                f"Value '{value}' not registered"
            )

        return self.value_to_id[value]

    def get_value(
            self,
            value_id: int,
    ) -> Any:

        """
        Get original value.
        """

        if value_id not in self.id_to_value:
            raise ValueIdNotFoundError(
                f"ValueID '{value_id}' not found"
            )

        return self.id_to_value[value_id]

    def contains(
            self,
            value: Any,
    ) -> bool:

        """
        Check value existence.
        """

        return value in self.value_to_id

    def contains_id(
            self,
            value_id: int,
    ) -> bool:

        """
        Check ID existence.
        """

        return value_id in self.id_to_value

    def size(
            self,
    ) -> int:

        """
        Number of registered values.
        """

        return len(
            self.value_to_id
        )

    def clear(
            self,
    ) -> None:

        """
        Clear registry.
        """

        self.value_to_id.clear()

        self.id_to_value.clear()

        self.next_id = 0

    def snapshot(
            self,
    ) -> dict[int, Any]:

        """
        Export registry snapshot.
        """
        return dict(self.id_to_value)
