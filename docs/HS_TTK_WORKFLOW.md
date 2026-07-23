# 海思 torch_npu + TTK 工作流

## 设计

原 ACLNN 能力保持默认不变：通用约束提取与执行默认仍使用 `atk`；也可显式指定
`--operator-family aclnn --test-framework ttk` 进入原生 TTK ACLNN 模式。海思
`torch_npu.*` 使用独立提示词、约束优先场景 profile 和 TTK
E2E CSV，避免两套参数语义互相污染。

| API | family | framework | 用例格式 |
|---|---|---|---|
| `aclnn*` | `aclnn` | `atk`（默认）或显式 `ttk` | compact JSON / ACLNN CSV |
| 六个重点 `torch_npu.*` 算子 | `hs`（CLI 亦接受 `torch_npu`） | `ttk` | E2E CSV |
| 其余 `torch_npu.*` API | `hs`（CLI 亦接受 `torch_npu`） | `constraints` | `constraints.json` |

## 初始化与提取

```bash
python scripts/init_run.py operator_docs/hs/torch_npu-npu_fused_infer_attention_score.md --mode mock
```

文档首行或文件名含 `torch_npu` 时，自动选择 `hs` 和隔离提示词；六个已有 adapter 的
重点算子自动选择 `ttk`，其余 API 自动选择 `constraints`（仅约束提取）。提示词使用
`prompts/torch_npu_constraints_extract_vN.md` 的最新数值版本，并由
`scripts/select_torch_npu_prompt.py` 装配通用文档知识和命中的算子知识。也可用
`--operator-family torch_npu`（`hs` 为兼容名）、`--test-framework` 显式覆盖。
选择结果和模块清单会写入 `run_state.json`。

torch_npu 装配器只读取 `knowledge/torch_npu/**`，不会读取 ACLNN 的
`prompts/modules/**`；ACLNN 装配器也不会读取 torch_npu 知识。显式 `--prompt` 是原样
复制的逃生口，不隐式追加任何模块。

海思约束输出 schema 仍为 `OperatorRule`，但必须从 Python 函数原型确定
optional/default，并按场景绑定 layout、shape、dtype、量化/稀疏模式和 optional tensor。

## 生成

```bash
python scripts/generate_cases.py \
  --constraints runs/<run>/iter_001/constraints.json \
  --output runs/<run>/iter_001/cases_ttk.csv \
  --count 1 --test-framework ttk \
  --server-config servers.json
```

生成器会为全部 `product_support` 平台保留独立 JSON，但 canonical `cases.json` 和
`cases_ttk.csv` 优先选择 `servers.json` 实际覆盖的平台，不再固定取第一个产品。
优先级为：显式 `--platform` > servers 文件顺序 > 单台服务器 platforms 顺序。

默认使用 `--hs-scenario-mode planned`，按 `tnd` / `bsnd` / `paged_attention`
拆分预算、固定 layout 并投影完整场景。如需完全使用原有
`agent/generators` 生成逻辑，执行：

```bash
python scripts/generate_cases.py \
  --constraints runs/<run>/iter_001/constraints.json \
  --output runs/<run>/iter_001/cases_ttk.csv \
  --count 10 --test-framework ttk \
  --hs-scenario-mode original \
  --server-config servers.json
```

`original` 模式不拆分场景、不固定 layout、不调用 HS case 投影。
生成阶段的 HS 语义、场景/domain 覆盖和 JSON→TTK CSV 转换审计仅记录
告警，不中断用例产出；直接进入 TTK 执行观察真实结果。

约束提取 prompt 面向目录内全部 torch_npu API；当前 TTK 用例适配重点支持 FIA、
MLA Prolog v3、LI、QLI、SFA、KV-SFA。所有框架共用正式生成器输出的
`cases.json` 具体场景；TTK adapter 再生成 CSV。每个平台 JSON、转换审计、Golden
manifest 均保留。禁止复制 baseline 凑数。

KV-SFA 会将用例预算拆成 `tnd`、`bsnd`、`paged_attention` 三个互斥场景。场景投影
不仅固定 layout，还同步绑定 rank、固定 D、batch/token 轴、`block_table`、actual
sequence tensor、保留的 None tensor 槽和整数数据值域。投影后的 JSON 会覆盖同场景
checkpoint，保证 checkpoint 与最终 `cases_<platform>.json` 一致。生成阶段会分别审计
每个产品平台；任一平台存在非法用例或缺少计划场景都会在 TTK CSV 转换前失败。

审计分两层：算子专项检查负责 dtype、固定维度和当前 schema 无法表达的内容语义；
通用 HS 关系复核器负责逐条执行所选平台的全部 `constraints_in_parameters`，
结果仅写入审计供失败后定位，不在生成阶段 fail-closed。覆盖域从约束中读取并在最终投影后记录到
`generation_summary.hs_domain_coverage`，不再以“场景存在”代替 dtype/head/mode/边界覆盖。

KV-SFA 当前通过 TTK `input_data_ranges` 只能可靠构造单元素 actual sequence，因此 TND/PA
暂时限定为精确 B=1；`sparse_indices` 使用固定合法值，PA `block_table` 随机范围被限制在
`[0, block_num-1]`。多 batch 前缀和与有效/无效索引排序必须等待 literal tensor builder，
对应限制会写入转换审计和生成摘要，不能静默宣称已覆盖。

## 执行准备

```bash
python scripts/execute_cases.py --test-framework ttk --generate \
  --cases runs/<run>/iter_001/cases_ttk.csv \
  --output runs/<run>/iter_001/execution_result.json
```

对于已经生成完三个（或多个）平台 JSON 的旧任务，无需重新 EXTRACT/GENERATE。
执行准备会按服务器覆盖平台自动复用对应 `cases_<platform>.json`，并重建 canonical
`cases.json` 与 `cases_ttk.csv`；平台切换证据写入 `platform_retarget`。

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
- 当前默认优先加载算子目录中的自主推导或源码 Golden，但不要求 manifest
  `verified`，精度不通过只记录诊断，不阻塞 NPU 功能执行结果。
- 内部格式运行时初始化与 Golden 插件解耦：无论是否使用 Golden，TTK 每个
  worker 都会设置 `allow_internal_format=True`。
- Golden 签名要接收 API 全部默认属性，避免 ParamPlan 默认值造成调用失败。
- 量化、PA、RoPE 等场景必须有匹配的专用 Golden，禁止靠放宽 ptol 掩盖差异。

## Golden 推导（当前非必需）

默认使用当前可用的 Golden，并将功能结果与精度结果分别统计。显式传入
`--no-golden` 可以不加载算子 Golden，但仍保留内部格式运行时初始化。

失败分类：场景违反文档回到约束迭代；JSON→CSV映射错误归 `ttk_adapter`；Golden
UNSUPPORTED/GOLDEN_FAILURE/数值公式问题归 `golden_derivation`；SSH/CANN/TBE归
`execution_environment`。只有第一类允许优化约束提取 prompt。

## 当前边界

FIA 非量化 BNSD 与 MLA Prolog V3 BF16 PA_BSND baseline 已完成真实精度闭环。其余
算子/场景仍需各自 CPU Golden 和结构化输入实现；未具备时不能把“CSV 可解析”报告成
“精度正确”。
