---
description: 以 ATK 或 TTK 模式准备/执行用例，并输出 execution_result.json。
---

# 用例执行规范

real 模式已拆为 **generate → 推导 → real-run** 三步：生成、CPU golden 推导、上传执行
三者分离，避免 real 重生成覆盖推导结果。禁止在 dummy 块未清除时跑 real-run。

平台选择：生成阶段可能已有多个 `cases_<platform>.json`，但执行阶段只跑一个平台。
默认不要传 `--platform`，执行器会按 `servers.json` 里服务器 `platforms` 数组顺序，
选择第一个已有 per-platform 用例且被服务器覆盖的平台。若旧的 `cases_ttk.csv` 由其他
平台生成，执行器自动复用匹配平台的 `cases_<platform>.json`，重组 `cases.json` 和
`cases_ttk.csv`，不重跑 EXTRACT 或正式用例生成。`--platform` 只作为人工覆盖项。

## real 模式三步

### 1. generate（生成 executor + expanded）

```text
python scripts/execute_cases.py --generate \
  --cases <iter>/<any-generated-cases-json> \
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
  --cases <iter>/<any-generated-cases-json> \
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

## TTK 分支

当 `run_state.json.test_framework == "ttk"` 时，不执行上面的 ATK generate/golden 流程：

先确认 `cases_ttk.csv` 存在且可读。当前 HS/E2E 默认只做 NPU 功能运行，
不要求 `golden_manifest.json`，不调用 `derive-ttk-golden`，不做精度/覆盖率
门禁。ACLNN 同样直接使用 TTK 原生 ACLNN runner。

HS/E2E 在 command preparation 和 real 执行前都会重新核对服务器平台。如果
`generation_summary.selected_platform` 不被服务器覆盖，但其他
`per_platform_files` 有匹配桶，则自动 retarget；结果记录在
`execution_result.platform_retarget`。

```text
python scripts/execute_cases.py --test-framework ttk --generate \
  --cases <iter>/cases_ttk.csv --output <iter>/execution_result.json
```

真实执行使用：

```text
python scripts/execute_cases.py --test-framework ttk --mode real \
  --cases <iter>/cases_ttk.csv --output <iter>/execution_result.json \
  --server-config servers.json
```

默认允许使用自主推导或源码 Golden，但精度失败只记录、不阻塞功能执行。
只有明确要求完全跳过 Golden 时，才在命令中追加 `--no-golden`；该选项不得
关闭 TTK worker 的内部格式运行时初始化。

远端目录由 `servers.json.ttk.remote_root` 控制，单次目录名为算子名_时间点；结果与日志
HS/E2E 结果下载到 `<iter>/ttk_artifacts/`；ACLNN 结果下载到
`<iter>/ttk_aclnn_artifacts/`。不得自动回退 ATK 或 mock；只有 run_state 明确为
`mode=mock` 时才执行 TTK mock。
