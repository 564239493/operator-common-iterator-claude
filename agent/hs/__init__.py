"""HiSilicon torch_npu operator support."""

from .ttk_generator import (
    HS_OPERATORS, install_ttk_plugin, is_hs_operator, resolve_ttk_plugin,
)
from .constraint_validation import validate_hs_constraints
from .case_validation import validate_hs_cases
from .scenario_planner import HSScenario, plan_hs_scenarios, pin_scenario_constraints

__all__ = [
    "HS_OPERATORS", "install_ttk_plugin", "is_hs_operator", "resolve_ttk_plugin",
    "validate_hs_constraints", "validate_hs_cases", "HSScenario",
    "plan_hs_scenarios", "pin_scenario_constraints",
]
