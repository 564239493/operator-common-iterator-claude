# torch_npu 文档约定与通用审校知识

这份知识只服务于 Python 端 `torch_npu` API 文档。它不是 ACLNN 规则的补丁，也不得把 ACLNN 的 workspace、两段式 C 接口、物理存储格式或参数命名习惯带入结果。

## 1. 26.0.0 文档的实际结构

- 算子文档通常按“产品支持情况 / 功能说明 / 函数原型 / 参数说明 / 返回值说明 / 约束说明 / 调用示例”组织，但并非每篇都具有全部章节。
- 参数表是首要约束来源之一。大量文档即使没有“约束说明”，仍会在参数描述中写 dtype、维度、shape、取值范围、场景必选、布局和跨参数关系。
- HTML 表格、合并单元格和场景表不是装饰。Attention、量化、MLA 等算子的可行组合经常只在表格中出现；必须按“同一行或同一场景列”保存组合关系。
- 章节解析必须识别 fenced code；代码示例中以 `##` 开头的 Python 注释不是新章节。
  HTML 表中的 `rowspan/colspan` 和 `<br>` 先展开语义再提取，不可只保留纯文本碎片。
- Beta 文档常缺少返回值、约束或完整示例。缺失表示文档证据不足，不表示没有约束，更不允许用相似算子补全。
- CANN、HDK、Ascend Extension 或包版本门槛以及废弃/替代信息属于生命周期范围；当前
  schema 无专门字段时保留原文并标记 `SCHEMA_GAP`，不能改写成 shape 或产品参数。
- 一级标题可能写成 `torch_npu-npu_xxx`，但函数原型写 `torch_npu.npu_xxx`；`operator` 一律以函数原型中的真实可调用名为准。
- 少数原型使用 `torch.npu.*` 或裸 `npu_*`。保留原型证据，不擅自改写命名空间；只有标题连字符异常而原型明确时才采用原型名。

## 2. 语言强度

下列表述通常形成硬约束：

- “必须 / 只能 / 仅支持 / 不支持 / 需要 / 不允许 / 不能 / 应满足 / 取值范围 / 取值为 / 与……一致 / 倍数 / 对齐 / 必选 / 无需传入 / 传入无效”。
- “当……时……”和场景表中的条件单元格。它们必须写成条件关系或互斥场景，不能升级成全局规则。
- “支持”列表应转换为闭集枚举，但只约束文档明确列举的维度，例如支持的 layout 不等于支持的 storage format。

下列表述默认不是硬约束：

- “建议 / 推荐 / 为获得更好性能 / 最好 / 一般 / 通常”。只写 `description`，除非同一段另有明确的有效性要求。
- 示例中的具体尺寸、随机分布、设备调用写法和输出数值。示例只用于验证解释，不得单独生成范围或相等关系。
- “默认值”只说明省略时的行为，不自动说明这是唯一支持值。

“用户需保证”“算子不做校验”“否则结果未定义”仍是输入前置条件，应提取为硬约束，并在描述中保留责任边界。

## 3. 签名与参数表联合读取

- 以函数原型确定参数顺序、位置参数/关键字参数边界、显式默认值和返回槽位；以参数表补充类型、shape、dtype、layout、范围及条件关系。
- 原型没有 `=...` 的参数默认必填。参数表误写“可选”时不可静默改变调用结构，应记录 `DOC_CONFLICT`。
- `*` 之后仅表示 keyword-only，不表示可选。没有 `*` 时也不能因为模板文字称“关键字参数”而篡改原型。
- `None` 默认值表示调用层可省略；在某些场景“必须传入”应表达为条件 presence 关系，而不是把参数改成全局 required。
- 末尾标量默认值必须保留在参数 `description`/相关 `src_text`；当前 schema 没有
  `default_value` 字段，不得自行新增。其合法取值仍由 `allowed_range_value` 或关系单独表达。
- 当前生成器的 `allowed_range_value.type=range` 固定采用严格开区间。文档闭区间或半开
  区间必须用关系表达并令 allowed range 为空，不能用 `[[lo,hi]]` 近似。
- 只有文档参数进入 `inputs`。B、S、N、D、T、block_num 等符号是 shape 轴变量，不得伪造成隐藏输入。

## 4. torch_npu 常见类型

- `Tensor` -> `aclTensor`；`List[Tensor]`、TensorList 或明确的 Tensor 序列 -> `aclTensorList`。
- `int` / `int64` -> `int`；`float` / `double` -> 保留 `float` / `double`；
  `bool` -> `bool`；`str` / `string` -> `string`。不要虚构项目当前生成器没有定义的
  `aclInt`、`aclFloat`、`aclBool`、`aclString` 类型。
- `List[int]`、`tuple[int]` 等整型序列优先使用当前 schema 已有的整数数组类型；若 schema 无法忠实表示，必须保留原始类型文字并标记 `SCHEMA_GAP`，不可压成单个整数。
- `Scalar`、`ScalarType`、`torch.dtype`、`Dict`、Python 对象和联合类型应按当前 schema 能力保守映射，并在描述中记录原始声明与缺口。
- Tensor 的 logical layout（如 BSND、TND、PA_BSND）属于 shape 语义；ND、FRACTAL_NZ 等属于 storage format。二者禁止混写。

## 5. Shape、列表与返回值

- `dimensions` 表示离散 rank 集合，不表示 rank 区间。文档写 1~8 维时展开为 `[1,2,3,4,5,6,7,8]`。
- 对 Python `List[int]`、tuple、aclIntArray 等非 Tensor 容器，“shape 为 `[N]`”或
  “长度为 N”表示容器长度，固定长度写 `array_length.value=[N]`；不得写入
  `dimensions`。长度区间才写 `array_length.value=[[lo,hi]]`。
- `allowed_range_value` 只描述容器元素值域。不得把 list shape/长度写进该字段，也不得
  把 optional 的 `None/null` 当成元素候选；presence 使用 `param is None` / `is not None`。
- 文档只给符号 shape 时，先写 rank，再用 `shape[i]` 关系表达轴约束。不同参数复用 B/S/N/D 符号时，必须显式写相等关系。
- TensorList 的“每个元素”约束应表达为当前 schema 可承载的最接近形式，并标记无法逐元素量化的部分；不要把 list 长度、元素 rank 和元素 shape 混为一谈。
- Python 整型列表可能表示普通长度列表，也可能表示 TND 累积序列。只有文档明确“前缀和/累计值”时，才添加单调递增、最后一项等于 T 或长度等于 B 等关系。
- block 数或表宽的 ceil-div 只在分子非负且除数可展开成正整数常量时写
  `(x+c-1)//c`；动态 `ceil/max` 或变量除数无法安全展开时保留 `SCHEMA_GAP`。
- 空 Tensor、`None`、长度 0 的 TensorList 是三种不同状态。文档分别支持时不得合并。
  当前生成器硬编码 Tensor 每轴 `>0`，zero-extent Tensor 只能保留固定槽/rank和
  `GENERATOR_GAP` 说明，不能写 `shape[i]==0` 使场景 UNSAT。
- 返回 `(Tensor, Tensor, ...)` 时保持固定槽位数量。某一场景结果“无效”通常仍是返回槽位，只在描述或条件约束中说明无效/占位，不能把固定 tuple 改成可变 tuple。
- 原地或状态更新语义需要保留在 `description` 和关系中；当前 schema 若不能表达输出别名或副作用，应标记 `SCHEMA_GAP`。

## 6. 场景、冲突与保守性

- 先建立场景键：产品、训练/推理、layout、稠密/PA/TND、量化模式、可选参数 presence、返回开关。场景键相同的一组 dtype/shape/presence 事实作为一个 AND 分支；多个合法场景组成 OR。
- 表格按列关联的 dtype、量化模式和 presence 不得拆成互不相关的全局枚举，否则会生成文档并不支持的笛卡尔积。
- `仅支持 A` 与签名默认 B、参数段枚举和场景表发生冲突时，分别保留证据并写 `DOC_CONFLICT`。禁止根据“更合理”猜一个结论。
- 若文档未定义哨兵值、无效区填充值、输出 dtype/shape 或某个场景的行为，写 `DOC_GAP`，不要从同族算子推断。
- 专项知识模块中的数字和场景仅用于提醒审校当前文档。若输入文档版本发生变化，以输入文档为准，并在变化处按证据重新提取。

### 6.1 Bool 值关系的表达语法

- 参数或参数属性与布尔常量比较时必须使用值比较：`flag == False`、
  `flag.range_value == True`、`flag != True`。
- 禁止使用 `flag is False`、`flag is not True` 等对象身份判断，也不要用
  `not flag` 替代明确的参数值关系。
- `is` / `is not` 只用于 `None` presence 判断；`True`/`False` 必须使用
  `==` / `!=`。条件分支同样遵守此规则。
- 例如“`return_value=False` 时 `layout_key` 不能为 `PA_BSND`”应写为
  `(return_value == False) or (layout_key.range_value != "PA_BSND")`，不得写
  `(return_value is False) or ...`。

### 6.2 Optional presence 与有效表达式

- optional 参数统一写 `param is None` / `param is not None`；禁止在提取结果中使用
  `param.is_present`，因为它属于生成器内部实现，不是文档或稳定 schema 字段。
- 属性约束使用短路形式，例如
  `(param is None) or (len(param.shape) == 1)`；条件必传使用
  `(layout.range_value != "TND") or (param is not None)`。
- `expr` 必须是完整、可解析的 Python 布尔表达式。禁止添加 `# TODO:`/`TODO:`
  前缀来绕过生成器；若存在生成器能力缺口，保留真实表达式，并把
  `[GENERATOR_LIMITATION:原因]` 写入 `src_text` 或参数 `description`。

## 7. 最低审校闭环

完成 JSON 后至少反查：函数原型的每个参数和返回槽位、参数表每一行、所有 HTML/Markdown 场景表、约束说明每一条、示例是否暴露了解释矛盾。随后验证所有关系中的参数名、shape 轴、dtype 枚举、默认值和 presence 方向均能被当前 schema/校验器接受。
额外搜索全部 `expr`，确保不存在 `is True`、`is False`、`is not True` 或
`is not False`；发现后必须改为对应的 `==` / `!=` 值比较。
同时确保不存在 `.is_present` 和以 `# TODO:`/`TODO:` 开头的 `expr`。
