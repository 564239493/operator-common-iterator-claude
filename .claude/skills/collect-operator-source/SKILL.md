---
description: 给定 operators-src 树与 aclnn 名，确定性收集算子全量源码（aclnn 接口/L0 host 实现/op_host/op_kernel/aicpu/fusion/tiling/binary/tbe/声明头）。include 不动点闭包 + canndev 多层 + 后缀变体 + L0 反查，产快照+manifest+报告。仅项目内 operators-src 树，只读；外部 SDK 头标 external/missing 不假装拉到。
---

# 算子源码全量收集规范

`source-analyst` / `analyze-source` 的快照由 `init_run --src` 生成。树根+算子名
场景下 `init_run` 已默认调用本 skill 的 `collect_operator_source.collect`（include
不动点闭包 + canndev 多层 + 后缀变体 + L0 反查），跨目录 L0 host 实现默认进快照。
**但仍有两个缺口需本 skill 手动补救**：

1. **L0 反查 stem 不一致盲区**：`l0op` 函数与声明头不同 stem 时，按头 stem 反查
   拿不到实现。典型 `aclnnNpuFormatCast` 调 `l0op::ViewCopy`，声明在
   `aclnn_kernels/contiguous.h`、实现在 `conversion/view_copy/op_api/view_copy.cpp`
   （stem=`view_copy`）——默认流程漏，需 `--extra-stem view_copy`。
2. **单算子目录 `--src`**：当 `--src` 直接指算子目录（非树根）时，`init_run` 走
   回退路径（SEED + L1 stem 闭包，不解析 include），跨目录 L0 全丢，需用本 skill
   传树根重跑。

本 skill 用确定性脚本把收集升级为 **include 依赖图不动点闭包 + canndev 多层 +
后缀变体 + L0 反查**，做到"尽可能完整"。文件收取必须确定性可复跑（不靠 LLM
找文件，否则不可复现）；LLM 只读报告、补外部头决策、衔接下游。

## 触发条件

- 用户要"完整获取某算子所有源码"；或
- `source-analyst` 报告 `missing_evidence`、`raw_checks` 偏少，怀疑快照缺跨目录
  L0 实现，需扩快照重跑。

## 第一步：确定性收集（Bash）

```bash
python scripts/collect_operator_source.py \
  --aclnn-name <aclnnNpuFormatCast> \
  --src-tree <operators-src 绝对路径> \
  --out <runs/<run-id>/src_snapshot> \
  [--with-tests] [--with-tbe-py] [--with-external-stub]
```

默认范围（推荐，开关默认关）：含后缀变体 `_def/_tiling_arch35/_dsl` + canndev
多层（aicpu/op_tiling/op_proto/op_host/fusion_pass/kernel binary/op_api/inc）
+ include 不动点闭包 + L0 反查；**不含** tests/examples、TBE Python、external
stub 副本头（这三类留开关，默认关以保持快照干净）。

产出（写在 `--out` 下）：
- **快照目录**：复制文件保相对路径，可直接喂
  `extract_source_constraints.py --snapshot <out>`。
- **`manifest.json`**：每文件 `{rel_path, layer, strategy, included_by[]}`。
  `layer ∈ {aclnn_api, l0_impl, op_host, op_kernel, op_graph, aicpu, fusion,
  tiling, binary_config, tbe_dsl, header_decl, external_stub, other}`；
  `strategy ∈ {seed, stem_closure, include_closure, l0_backref, external_stub}`。
- **`closure_report.md`**：阶段统计 + 分层命中数 + external/missing 清单。

## 第二步：读 `closure_report.md` 做决策

1. `include_closure` / `l0_backref` 数为 0 → 定位或解析可能失败，回看
   `operator_dirs` 是否正确、include 搜索根是否覆盖。
2. `external`（`opdev/`/`graph/`/`securec.h` 等）= 项目内未提供实现的外部 SDK 头
   → **诚实提示用户**从 CANN 安装目录补，不假装拉到。
3. `missing`（include 在项目内完全找不到）→ 多为系统头或命名差异，记录在案，
   不阻塞。
4. `l0_impl` 层数应 ≥ 该算子实际调用的 L0 op 数（npu_format_cast 应见
   transdata/contiguous/reshape/transpose/view_copy 五个）；明显少 → 反查
   stem_index 可能漏，检查 `aclnn_kernels/<x>.h` 是否命中 `namespace l0op`。

## 第三步：衔接下游

若供 `source-analyst` 用：
1. 把 `<out>` 绝对路径回填 `run_state.operator_src_snapshot`。
2. 重跑 `python scripts/extract_source_constraints.py --snapshot <out> --out
   <iter-dir>/source_raw.json` —— 闭包扩大后 `raw_checks` 应增多（对照
   `closure_report` 的 layer 命中）。
3. 再走 `analyze-source` skill 的 LLM 判读与 3-markdown 产出。

## 已知局限（`l0_backref` 按 include 头 stem 反查）

当某 `l0op` 函数与其声明头**不同 stem** 时，按头 stem 反查只能拿到同 stem 实现，
漏掉共声明的实现。典型：`aclnnNpuFormatCast` 调 `l0op::ViewCopy`，但 `ViewCopy`
与 `Contiguous` 共声明于 `aclnn_kernels/contiguous.h`，实现在
`conversion/view_copy/op_api/view_copy.cpp`（stem=`view_copy`）——反查 `contiguous`
拿到 `contiguous.cpp`，拿不到 `view_copy.cpp`。

补救：`--extra-stem view_copy` 把该 stem 显式加入闭包，阶段2 即拉 `view_copy.cpp`
+ 其 include 闭包。手动核对 manifest 是否缺某 `l0op` 函数实现，缺则按其声明头
定位实际 stem 补 `--extra-stem`。函数名→文件 stem 多数 PascalCase→snake_case
一致（ViewCopy→view_copy），个别不一致（TransData→transdata，靠头 stem 兜底）。

## 边界

- 只读 `--src-tree`；只写 `--out`（快照 + manifest + report）。不改算子源码。
- L0 反查**只拉实现 `.cpp/.cc` 本身 + 其 include 闭包**，不整目录拉，避免误入
  同目录无关的别的 aclnn 接口层（如 `trans_data/op_api/aclnn_trans_matmul_weight.cpp`）。
- external/missing 诚实标注，不猜测、不强行拉预编译库。
- include 闭包只对项目内真源码头递归；`opdev/`/`graph/`/`securec` 前缀与裸
  SDK 头名记清单不递归，防发散。
- 不引入 LLM 找文件；找文件全部由脚本确定性完成。
