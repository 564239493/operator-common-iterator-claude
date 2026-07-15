# 海思 torch_npu + TTK 工作流

## 设计

原 ACLNN 能力保持默认不变：通用约束提取、generator、ATK executor 和远程 ATK
执行仍使用 `atk`。海思 `torch_npu.*` 使用独立提示词、约束优先场景 profile 和 TTK
E2E CSV，避免两套参数语义互相污染。

| API | family | framework | 用例格式 |
|---|---|---|---|
| `aclnn*` | `aclnn` | `atk`（默认） | compact JSON |
| 六个 `torch_npu.*` 海思算子 | `hs` | `ttk` | E2E CSV |

## 初始化与提取

```bash
python scripts/init_run.py operator_docs/hs/torch_npu-npu_fused_infer_attention_score.md --mode mock
```

文档首行或文件名含 `torch_npu` 时，自动选择 `hs`、`ttk` 和
`prompts/hs_constraints_extract_v1.md`。也可用 `--operator-family`、
`--test-framework` 显式覆盖。选择结果会写入 `run_state.json`。

海思约束输出 schema 仍为 `OperatorRule`，但必须从 Python 函数原型确定
optional/default，并按场景绑定 layout、shape、dtype、量化/稀疏模式和 optional tensor。

## 生成

```bash
python scripts/generate_cases.py \
  --constraints runs/<run>/iter_001/constraints.json \
  --output runs/<run>/iter_001/cases_ttk.csv \
  --count 1 --test-framework ttk
```

当前支持 FIA、MLA Prolog v3、LI、QLI、SFA、KV-SFA。所有框架共用正式生成器输出的
`cases.json` 具体场景；TTK adapter 再生成 CSV。每个平台 JSON、转换审计、Golden
manifest 均保留。禁止复制 baseline 凑数。

## 执行准备

```bash
python scripts/execute_cases.py --test-framework ttk --generate \
  --cases runs/<run>/iter_001/cases_ttk.csv \
  --output runs/<run>/iter_001/execution_result.json
```

Linux CANN/NPU 节点执行：

```bash
python3 -m ttk e2e -i cases_ttk.csv --validate
python3 -m ttk e2e -i cases_ttk.csv --backend npu
```

## 远程真实执行

`servers.json` 中每台服务器可配置：

```json
"ttk": {
  "remote_root": "/home/operator_ttk/runs",
  "repo_path": "/home/operator_ttk/ops-test-kit",
  "python": "python3",
  "env_init_script": "/usr/local/Ascend/ascend-toolkit/set_env.sh"
}
```

每次执行创建 `<remote_root>/<算子名>_<YYYYmmdd_HHMMSS>/`，上传 CSV 与 Golden
插件，在其中生成 `results.csv` 和 `log/`。真实执行：

```bash
python scripts/execute_cases.py --test-framework ttk --mode real \
  --cases <iter>/cases_ttk.csv --output <iter>/execution_result.json \
  --server-config servers.json
```

完成后同步到 `<iter>/ttk_artifacts/`：`results.csv`、`log/`、远端 stdout/stderr。

## 精度与 Golden 经验

- `precision_tolerances` 是 `(rtol, ptol)`；`atol` 写在 `absolute_precision`。
- NPU-only `torch_npu.*` 必须提供 E2E Golden 插件并传 `--plugin`。
- Golden 签名要接收 API 全部默认属性，避免 ParamPlan 默认值造成调用失败。
- 量化、PA、RoPE 等场景必须有匹配的专用 Golden，禁止靠放宽 ptol 掩盖差异。

## Golden 推导门禁

每个算子使用独立插件和 `golden_manifest.json`。manifest 未验证时，EXECUTE 阶段调用
`derive-ttk-golden`，依次完成公式推导、插件实现、CSV validate、单场景真实 NPU 精度
验证，之后才允许批量执行。

失败分类：场景违反文档回到约束迭代；JSON→CSV映射错误归 `ttk_adapter`；Golden
UNSUPPORTED/GOLDEN_FAILURE/数值公式问题归 `golden_derivation`；SSH/CANN/TBE归
`execution_environment`。只有第一类允许优化约束提取 prompt。

## 当前边界

FIA 非量化 BNSD 与 MLA Prolog V3 BF16 PA_BSND baseline 已完成真实精度闭环。其余
算子/场景仍需各自 CPU Golden 和结构化输入实现；未具备时不能把“CSV 可解析”报告成
“精度正确”。
