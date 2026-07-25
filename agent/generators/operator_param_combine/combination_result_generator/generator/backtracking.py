from __future__ import annotations

from typing import Any, List, Optional, Tuple

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import PARAMETER_ATTRIBUTES


class BacktrackingGenerator:

    def __init__(
        self,
        config: GeneratorConfig,
        constraint: Optional[ConstraintProtocol] = None,
        *,
        max_depth: int = 10000,
    ) -> None:
        if config is None:
            raise ValueError("config cannot be None")
        self._config = config
        self._constraint = constraint
        self._max_depth = max_depth
        self._variables = self._build_variable_list()
        self._depth_count = 0

    def _build_variable_list(self) -> List[Tuple[str, str, Tuple[Any, ...]]]:
        variables: List[Tuple[str, str, Tuple[Any, ...]]] = []
        for param in self._config.parameters.values():
            for attr in PARAMETER_ATTRIBUTES:
                domain = getattr(param, attr.value)
                if domain:
                    variables.append((param.name, attr.value, domain))
        return variables

    def generate(
        self, fixed_values: Optional[dict[str, dict[str, Any]]] = None
    ) -> Optional[dict[str, dict[str, Any]]]:
        fixed_values = fixed_values or {}
        assignment: dict[str, dict[str, Any]] = {
            p.name: {} for p in self._config.parameters.values()
        }

        for pname, attrs in fixed_values.items():
            if pname not in assignment:
                assignment[pname] = {}
            assignment[pname].update(attrs)

        unassigned: List[Tuple[str, str, Tuple[Any, ...]]] = [
            (pn, an, dom)
            for (pn, an, dom) in self._variables
            if pn not in fixed_values or an not in fixed_values.get(pn, {})
        ]

        self._depth_count = 0

        if not unassigned:
            if self._is_valid(assignment):
                return assignment
            return None

        result = self._backtrack(assignment, unassigned, 0)
        return result

    def _backtrack(
        self,
        assignment: dict[str, dict[str, Any]],
        variables: List[Tuple[str, str, Tuple[Any, ...]]],
        depth: int,
    ) -> Optional[dict[str, dict[str, Any]]]:
        if depth >= self._max_depth:
            return None

        self._depth_count += 1

        if not variables:
            if self._is_valid(assignment):
                return assignment
            return None

        var = variables[0]
        pname, aname, domain = var
        remaining = variables[1:]

        for value in domain:
            assignment[pname][aname] = value

            if self._partial_valid(assignment, remaining):
                result = self._backtrack(assignment, remaining, depth + 1)
                if result is not None:
                    return result

            del assignment[pname][aname]

        return None

    def _is_valid(self, assignment: dict[str, dict[str, Any]]) -> bool:
        if self._constraint is None:
            return True
        return self._constraint.evaluate(assignment)

    def _partial_valid(
        self,
        assignment: dict[str, dict[str, Any]],
        remaining: List[Tuple[str, str, Tuple[Any, ...]]],
    ) -> bool:
        if self._constraint is None:
            return True
        remaining_vars = {(pn, an) for (pn, an, _) in remaining}
        non_empty = {
            p: {a: v for a, v in attrs.items() if v is not None and (p, a) not in remaining_vars}
            for p, attrs in assignment.items()
        }
        all_empty = True
        for attrs in non_empty.values():
            if attrs:
                all_empty = False
                break
        if all_empty:
            return True
        return True
