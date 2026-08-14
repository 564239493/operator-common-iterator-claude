"""HiSilicon torch_npu operator support."""

from .ttk_generator import (
    HS_OPERATORS, generate_ttk_cases, install_ttk_plugin, is_hs_operator,
    load_golden_manifest, resolve_ttk_plugin,
)
from .golden_coverage import audit_golden_coverage
from .constraint_validation import validate_hs_constraints
from .case_validation import validate_hs_cases
from .scenario_planner import HSScenario, plan_hs_scenarios, pin_scenario_constraints

__all__ = [
    "HS_OPERATORS", "generate_ttk_cases", "install_ttk_plugin",
    "is_hs_operator", "load_golden_manifest", "resolve_ttk_plugin",
    "audit_golden_coverage",
    "validate_hs_constraints", "validate_hs_cases", "HSScenario",
    "plan_hs_scenarios", "pin_scenario_constraints",
]
