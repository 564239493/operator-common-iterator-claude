---
description: 以 mock 或 real 模式执行 cases.json，并输出标准 execution_result.json。
---

# 用例执行规范（MCP 版）

real 模式已拆为 **generate → 推导 → 自检 → real-run** 四步：生成、CPU golden 推导、
自检、上传执行，四者分离。禁止在 dummy 块未清除时跑 real-run。

平台选择：生成阶段可能已有多个 `cases_<platform>.json`，但执行阶段只跑一个平台。
默认不传 `platform` 参数，执行器会按 `servers.json` 里服务器 `platforms` 数组顺序，
选择第一个与算子 `product_support` 匹配的产品用例。`platform` 只作为人工覆盖项。

## real 模式四步

### 1. generate（生成 executor + expanded）

调用 MCP 工具：
```
mcp__opci__execute_cases_generate(
  cases="<iter>/<any-generated-cases-json>",
  output="<iter>/generate_result.json",
  doc="<run>/inputs/<doc>.md",
  operator="<op>",
  server_config="servers.json",
  run_id="<run-id>"
)
```

产出 `<iter>/cases_executor.py`（含 dummy `# TODO: CPU_GOLDEN` 块）与
`<iter>/cases_expanded.json`。不连 SSH。

### 2. CPU golden 推导（atc-cpu-golden-derivation skill）

对 `<iter>/cases_executor.py` 调用 skill，doc 用 `inputs/<doc>.md` 快照。随后自检：

使用 Bash 做轻量检查：
```text
grep -E "_dummy_output|FALLBACK|TODO: CPU_GOLDEN" <iter>/cases_executor.py
python -c "import ast; ast.parse(open('<iter>/cases_executor.py',encoding='utf-8').read())"
```

调用 MCP 工具：
```
mcp__opci__validate_executor(path="<iter>/cases_executor.py")
```

三者全过（grep 无命中 + ast 退出 0 + valid:true）才进 real-run；否则重试推导最多 3 次；
仍不过则写 `execution_result.json`（status=error, engine_error="CPU golden 推导未完成"）并停止。

### 3. 自检

见上方第 2 步末尾的自检流程。

### 4. real-run（上传 + 跑 atk，不再重生成）

调用 MCP 工具：
```
mcp__opci__execute_cases_real(
  cases="<iter>/<any-generated-cases-json>",
  output="<iter>/execution_result.json",
  doc="<run>/inputs/<doc>.md",
  operator="<op>",
  server_config="servers.json",
  run_id="<run-id>"
)
```

real 不再自动生成 executor；iter_dir 缺 generate 产物时会短路报错。执行后：

```
mcp__opci__validate_execution(path="<iter>/execution_result.json")
```

真实执行是默认行为。配置缺失时停止并提示用户补充，禁止回退 Mock。只有用户明确传入
`--mode mock` 时，才运行 Mock 用例：

```
execute_cases_mock(cases="<cases.json>", output="<execution_result.json>")
```

执行后调用 `mcp__opci__validate_execution(path="<execution_result.json>")`。
网络、认证、环境和框架故障写入 `engine_error`，不要伪装成普通 case fail。
