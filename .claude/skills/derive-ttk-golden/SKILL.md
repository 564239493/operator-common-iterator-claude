---
name: derive-ttk-golden
description: Derive, implement, and validate a per-operator TTK E2E CPU Golden plugin for torch_npu operators. Use during the TTK EXECUTE stage when golden_manifest.json is missing/pending, TTK reports UNSUPPORTED/GOLDEN_FAILURE, or a new generated scenario is outside verified Golden coverage.
---

# Derive TTK Golden

Read the run's document snapshot, `constraints.json`, canonical `cases.json`, `cases_ttk.csv`, and any existing plugin/manifest. Never infer from chat history.

1. Select one concrete CSV scenario. Record its dtype, layout, quantization, optional inputs, output count, and side effects.
2. Derive the reference from documented formulas. Reuse `agent/hs/ttk_plugins/common/` primitives when present. Keep one plugin per operator.
3. Implement an exact API-compatible function. Accept every documented default keyword and reject unsupported modes with `NotImplementedError`; never approximate an unsupported mode.
4. Register it through `__golden__["e2e"][api_name]`. Enable required torch_npu runtime settings at module import.
5. Model intermediate dtype rounding, RoPE convention, quantization saturation, cache mutation, empty/null outputs, output order, shape, and dtype explicitly.
6. Copy the plugin beside `cases_ttk.csv`, run syntax checks and `ttk e2e --validate`, then run real NPU E2E. A derivation is not verified until shape/dtype match and precision passes.
7. On mismatch, use `results.csv` and downloaded logs to adjust the reference, not the extracted constraints, unless evidence proves the scenario itself violates the document.
8. Write/update `agent/hs/golden_manifests/<operator>.json` with only scenarios actually proven by real execution. Keep unsupported dimensions explicit.

Do not loosen precision merely to hide a wrong formula. A BF16-specific tolerance is acceptable only after output semantics match and the residual is explained by documented/intermediate rounding.

Return plugin path, manifest path, verified scenario identifiers, real execution directory, precision result, and unsupported coverage.
