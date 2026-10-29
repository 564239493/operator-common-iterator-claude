# 方案 v3：设备优先三段式场景收集（现行）

> 状态：**现行实现**。v2 的“一次文本输入 + 全量量化关键词面板”已废止；下文
> “v2 历史设计”仅作参考。现行流程见本节。

## v3 现行流程（覆盖下文 v2）

需求：从算子文档提取场景时，**必须先让用户选择该算子支持的设备类型**；设备确定后，
再去文档识别**该设备下**有哪些量化场景并让用户选择；最后可选参数取值。

1. **设备（强制）**：读“产品支持情况”，只列支持（√）设备全称；`--scenes` 只先做
   设备对齐；对不上/多义 → 候选重选；无文本 → AskUserQuestion 选设备。设备未确定前
   禁止展示量化场景、禁止落盘/渲染/进 EXTRACT。
2. **量化场景（设备确定后）**：主协调器重读文档，按历史三段式的量化模板规则**只识别
   该设备下**的场景：场景=具体量化模板（分类词不算场景）；无设备标注场景合并到该设备；
   只保留文档给出参数取值约束的场景；过滤错误/校验场景；不臆造，其它设备专属场景不得
   进入候选。让用户选择（可多选）；该设备无任何量化场景 → 无场景维度，落盘 `null`。
3. **参数（可选）**：等值→fix、枚举→expand、大白话闭区间→展开整数序列。
4. 回显确认一次 → `selection.json` → `render_scene_directive.py`（结构校验）→
   `scene_directive.md` → EXTRACT 收窄。
5. **批处理**：批次级只确认设备；每个单算子 run 再按自己的文档识别该设备下场景并
   征询用户。

下文 v2 的“一次文本输入 + 全量关键词面板”已废止，仅作历史参考。

# 方案 v2：--scenes 场景直输（简化版：文字匹配 + 下游复用）

> 状态：**历史设计 v2（已被 v3 取代）**。取代 v1 中依赖 scene-scanner/scene_scan.json 的设计。
>
> 需求明确：
> ① `/iterate-operator` 新增 `--scenes "<场景描述>"`（如 "A2非量化groupListType等于2"）；
> ② **隐藏原 scene-scanner 解析 + Q1→Q2→Q3 征询**：无 `--scenes` 时让用户手动输入场景，
>    并给出提示（有哪些设备/场景可选）；
> ③ 场景识别用**简单文字匹配**（不做三级扫描），最小化修改；
> ④ 用户场景写入 `selection.json` 之后的逻辑（directive → extractor 收窄 → 生成器）**全部复用**。

---

## 1. 设计原则（三条）

1. **提示 = 模型读文件列清单（advisory）**：主协调器直接读文档快照的"产品支持情况"章节列出
   支持的设备、读文中出现的量化场景关键词、读 `servers.json` 的 `platforms` 并集标注哪些设备
   有执行机——这些只是给用户的**建议**，不建解析管线、不产 scene_scan.json；
2. **识别 = 简单文字匹配 + 回显确认**：设备关键字包含匹配、量化关键词命中、`参数=值/等于/取`
   模式提取；解析结果**回显给用户确认一次**后才落盘；
3. **下游全复用**：落盘为原值级 `selection.json` → `render_scene_directive.py`（唯一代码改动点：
   `--scan` 改为可选）→ `scene_directive.md` → extractor 收窄 → 生成器——与现有链完全一致。

**被隐藏（不删除）**：scene-scanner 委派、scene_scan.json、Q1→Q2→Q3、check_scene_conflicts.py。
skill/脚本文件原样保留在仓库中，SKILL 不再调用；将来需要时恢复调用即可。

## 2. 新流程

### 2.1 有 `--scenes "<描述>"`

```text
① init_run 透传 --scenes 原文 → run_state.scenes
② 主协调器读文档快照（产品支持情况/量化关键词）+ servers.json platforms → 组装建议（自用）
③ 文字匹配解析：<描述> → {设备全称, 量化场景, 参数取值[]}
④ 回显解析结果（结构化表格）→ AskUserQuestion 让用户【确认/修改】一次
⑤ 确认后写 selection.json（原值级格式）
⑥ render_scene_directive.py（不带 --scan）→ scene_directive.md + 回写 run_state.scene
⑦ 进入 EXTRACT：extractor 按 directive 收窄（device_types 交集 + param fix/expand），
   且只提取所选设备/场景的约束（指令见 §4）
```

### 2.2 无 `--scenes`（手动输入模式）

```text
①② 同上
③ 主协调器向用户展示提示面板（全部来自文件，非模型记忆）：
   - 文档支持的设备：产品支持情况章节的设备列表（全称）
   - 执行机支持的平台：servers.json 各服务器 platforms 并集（标注交集/差异）
   - 文中出现的量化场景关键词（如 非量化/全量化-A8W8/…）
④ 请用户输入场景描述，格式建议：设备类型（必要）+ 量化场景 + 参数取值
⑤ 用户输入 → 走 2.1 的 ③-⑦
```

## 3. 文字匹配规则（简单匹配，四条）

| 识别项 | 匹配规则 | 失败处理 |
|---|---|---|
| 设备 | 用户文本包含设备关键字（"A2"/"A3"/"910B" 或文档设备名子串）→ 对齐到文档"产品支持情况"的**全称**；同时与 servers.json platforms 比对，不在其中 → 警示"该设备无执行机"（MVP 不阻断） | 对不上任何设备 → 追问，附文档设备列表作为建议 |
| 量化场景 | 文本命中文档中出现的量化关键词（非量化/全量化-xx/伪量化…） | 匹配不上 → 展示关键词清单让用户挑；用户场景非量化算子则允许不填 |
| 参数取值 | 等值（"等于2"→fix）；枚举（"取0和3"→expand）；**闭区间大白话**（"2-6维"/"4-6之间"/"2~6"→展开整数序列 [2..6]，expand，跨度>32 须用户确认）；开区间话术（"以上/以内"）须请用户给确定边界 | 参数名对不上约束（extractor 阶段发现）→ 回报用户；MVP 不做枚举预校验（无 scan） |
| 多场景 | 暂不支持（MVP 单场景） | — |

**回显确认**是安全网：匹配结果以表格（设备全称 / 量化场景 / 参数取值列表）展示，用户确认后才写 selection.json——匹配错了在这一步被人拦住。

## 4. 代码改动（4 个文件，最小化）

| 文件 | 改动 | 量 |
|---|---|---|
| `.claude/skills/iterate-operator/SKILL.md` | argument-hint 加 `--scenes "<场景描述>"`；SCENE_SCAN 节整体替换为 §2 新流程（scene-scanner/Q123 段移入附录"已隐藏（legacy）"）；init_run 调用透传 `--scenes` | 净减 |
| `scripts/init_run.py` | 新参数 `--scenes`，原文写入 `run_state.scenes`；`scene.source` 字段 | ~10 行 |
| `scripts/render_scene_directive.py` | `--scan` 从必填改为可选：缺省时**跳过枚举交叉校验**，仅做结构校验（device_types 非空、selection 非空、param 值为数组），机读块与 `run_state.scene` 照常写（`scene.scan` 置空） | ~25 行 |
| `.claude/skills/extract-constraints/SKILL.md` | 加一句：directive 存在时**仅提取所选设备/所选场景的约束**，未选场景的专属条目不产出 | ~3 行 |

**不改动**：scene-scanner skill 与 prompts/scan_scenes.md（保留隐藏）、check_scene_conflicts.py（跳过不调）、flow_control.py、executer/、agent/generators/、execute/extract 等其余全部。

## 5. 数据结构

```jsonc
// run_state.json 新增/扩展
"scenes": "A2非量化groupListType等于2",   // 用户原始输入（--scenes），无则 ""
"scene": { ...
  "source": "scenes_param",               // scenes_param | interactive_input
  "scan": "",                             // 无 scan，置空
  ...其余字段不变（directive/selection/device_types 照旧）}

// selection.json —— 原值级格式，结构不变
{"device_types": ["Atlas A2 训练系列产品/Atlas A2 推理系列产品"],
 "selection": {"Atlas A2 训练系列产品/Atlas A2 推理系列产品":
   {"非量化": {"groupListType": [2]}}}}
```

## 6. 无 scan 后的安全网（为什么砍掉扫描仍是安全的）

| 风险 | 原防线（scan） | 新防线 |
|---|---|---|
| 设备名对齐错误 | scan 枚举校验 | 回显确认（人拦）+ extractor 的 product_support 交集为空时必然暴露并报告 |
| 参数名拼错 | scan 枚举校验 | 回显确认 + extractor 提取时发现参数对不上文档参数表 → 报告 |
| 场景间约束冲突 | check_scene_conflicts | param fix/expand 收窄后由 CHECK/REPAIR 的 Z3 取证暴露（矛盾→open issue） |
| prompt_v1 未变，提取范围未声明 | directive selection | §4 第 4 行的一句独占指令（仅提取所选设备/场景） |

即：**扫描管线删掉后，原来的"事前枚举校验"换成了"回显确认（人）+ 提取期暴露（extractor/CHECK）"**——错误仍会被拦住，只是发现点后移，换来的是零解析管线。

## 7. 实施分期与验证

| 期 | 内容 | 验收 |
|---|---|---|
| **M1** | `--scenes` 全链：init_run 透传 → 文字匹配 → 回显确认 → selection.json → render（无 scan 模式）→ extractor 独占指令 | GroupedMatmulV5 实测 `--scenes "A2非量化groupListType等于2"`：确认表正确对齐 A2 全称与"非量化"；directive 生成；constraints 只含 A2/非量化场景可达条目；cases 只出 groupListType=2 |
| **M2** | 无 `--scenes` 手动输入：提示面板 + 追问规则 | 无参启动展示设备/场景建议；故意输入文档没有的设备（如 A3）→ 追问并列出文档设备列表 |
| **M4（按需）** | 多场景语法、legacy（scene-scanner+Q123）回归开关 | 按需 |

## 8. 待确认（2 个）

| # | 问题 | 建议 |
|---|---|---|
| 1 | 设备名对不上文档任何设备时：追问重输（建议），还是警示放行 | 建议**追问重输**——设备是必要项且收窄源，错了下游全空 |
| 2 | extractor "仅提取所选场景"的力度 | 建议**一句话指令版**（软约束，靠 checker 复核兜底）；如需硬保证（逐条核对排除名单）再加 M3 |
