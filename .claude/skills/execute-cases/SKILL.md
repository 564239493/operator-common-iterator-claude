---
description: 以 mock 或 real 模式执行 cases.json，并输出标准 execution_result.json。
---

# 用例执行规范

real 模式已拆为 **generate → 推导 → real-run** 三步：生成、CPU golden 推导、上传执行
三者分离，避免 real 重生成覆盖推导结果。禁止在 dummy 块未清除时跑 real-run。

## real 模式三步

### 1. generate（生成 executor + expanded）

```text
python scripts/execute_cases.py --generate \
  --cases <iter>/cases_<platform>.json \
  --output <iter>/generate_result.json \
  --doc <run>/inputs/<doc>.md --operator <op> \
  --server-config servers.json --run-id <run-id>
```

产出 `<iter>/cases_executor.py`（含 dummy `# TODO: CPU_GOLDEN` 块）与
`<iter>/cases_expanded.json`。不连 SSH。

### 2. CPU golden 推导（atc-cpu-golden-derivation skill）

对 `<iter>/cases_executor.py` 调用 skill，doc 用 `inputs/<doc>.md` 快照。随后自检：

```text
grep -E "_dummy_output|FALLBACK|TODO: CPU_GOLDEN" <iter>/cases_executor.py
python -c "import ast; ast.parse(open('<iter>/cases_executor.py',encoding='utf-8').read())"
python scripts/validate_artifacts.py executor <iter>/cases_executor.py
```

三者全过（grep 无命中 + ast 退出 0 + valid:true）才进 real-run；否则重试推导最多 3 次；
仍不过则写 `execution_result.json`（status=error, engine_error="CPU golden 推导未完成"）并停止。

### 3. real-run（上传 + 跑 atk，不再重生成）

```text
python scripts/execute_cases.py --mode real \
  --cases <iter>/cases_<platform>.json \
  --output <iter>/execution_result.json \
  --doc <run>/inputs/<doc>.md --operator <op> \
  --server-config servers.json --run-id <run-id>
```

real 不再自动生成 executor；iter_dir 缺 generate 产物时会短路报错。执行后：

```text
python scripts/validate_artifacts.py execution <iter>/execution_result.json
```

真实执行是默认行为。配置缺失时停止并提示用户补充，禁止回退 Mock。只有用户明确传入
`--mode mock` 时，才运行 Mock 用例：

```text
python scripts/execute_cases.py --mode mock --cases <cases.json> --output <execution_result.json>
```

执行结束后运行 `python scripts/validate_artifacts.py execution <execution_result.json>`。
网络、认证、环境和框架故障写入 `engine_error`，不要伪装成普通 case fail。
