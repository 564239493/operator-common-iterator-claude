# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
版权信息：华为技术有限公司，版本所有(C) 2026-2026
修改记录：2026/3/26 10:05
功能：基于z3求解器求解SMT问题，约束表达式必须为标准的python合法表达式
使用示例：
    builder = Z3ConstraintBuilder()

    # 1. 声明变量 x，限制其 dtype 只能是 int8 或 uint8
    builder.declare_var("x", allowed_dtypes=["int8", "uint8"])

    # 2. 声明变量 y，限制其 dtype 只能是 float32
    builder.declare_var("y", allowed_dtypes=["float32"])

    # 3. 变量 z 未显式声明，将在使用时自动创建，默认支持所有类型

    # 添加约束
    builder.add_constraint('x.dtype == "int8"')  # 有效约束
    # builder.add_constraint('x.dtype == "float32"') # 这将导致 UNSAT，因为 float32 不在 x 的允许列表中

    builder.add_constraint('len(x.shape) == 2')
    builder.add_constraint('x[0] > 100')  # 触发 x 的数据约束，由于 x 是 int8，范围是 [-128, 127]，所以 x[0] 最大为 127

    builder.add_constraint('y[0] < 0.5')  # 触发 y 的数据约束

    # 求解
    builder.solve()
"""
import ast
from typing import Dict

import z3

from agent.generators.common_utils.expression_analysis import ExpressionPreprocessor
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.param_constraint_solve.expression_preprocess_utils import ASTtoZ3Converter, TensorVar, ScalarVar, \
    ListVar
from agent.generators.param_constraint_solve.param_var_definition import TensorListVar

logger = LazyLogger()

# all()/any() 定长展开的上限：超过该长度继续用量词，避免展开后子句数爆炸。
MAX_STATIC_UNROLL = 128


class Z3ConstraintBuilder:
    _VAR_FACTORY = {
        'tensor': (TensorVar, lambda self, kwargs: (self.solver, kwargs.get("dtype"), kwargs.get('allowed_dtypes'),
                                                    kwargs.get('allowed_formats'), kwargs.get('range_value'))),
        'tensor_list': (TensorListVar,
                        lambda self, kwargs: (self.solver, kwargs.get("dtype"), kwargs.get('allowed_dtypes'),
                                              kwargs.get('allowed_formats'), kwargs.get('range_value'),
                                              kwargs.get("length"))),
        'scalar': (ScalarVar, lambda self, kwargs: (self.solver, kwargs.get('dtype'), kwargs.get('range_value'))),
        'list': (ListVar, lambda self, kwargs: (self.solver, kwargs.get('dtype'), kwargs.get('range_value'),
                                                kwargs.get("length"))),
    }

    # （实测可解实例 ~30s 内出解；解不动的 300s 也解不动，90s 留足余量）。
    def __init__(self, timeout_ms=90000):
        self.solver = z3.Solver()
        self._timeout_ms = timeout_ms
        if timeout_ms:
            self.solver.set('timeout', timeout_ms)
        self.var_map = {}
        self._slice_counter = 0
        # 仅用于兼容历史 constraints 中已经存在的 ``# TODO:`` 前缀。
        # 新版 torch_npu 提示词禁止再生成该前缀，但恢复旧任务时不能因列表未初始化崩溃。
        self.dropped_constraints = []
        # 静态长度表：param -> 唯一确定的长度（确定性事实），供 all()/any() 定长展开。
        self.static_lengths = {}
        # 维度个数上限表：shape seq 常量名（如 "self.shape"）-> 文档声明的最大维度个数，
        # 来自已硬断言的 len(p.shape) in dimensions；仅供 range() 量词展开推导循环上界。
        self.static_rank_max = {}
        # 标量参数取值范围表：param -> (lo, hi)，来自硬约束里 `lo <= p.range_value <= hi`
        # 这类条款；仅供 range() 量词展开推导符号界的静态窗口（展开时会一并断言）。
        self.static_param_ranges = {}
        # 已断言过的 (参数名, lo, hi)：同一区间只断一次，避免展开多处时重复入库。
        self.asserted_param_domains = set()
        # 单次展开的元素上限，供转换器读取（避免反向 import 造成循环依赖）。
        self.unroll_limit = MAX_STATIC_UNROLL
        # 展开审计：(param, kind, length) 供日志与测试核对。
        self.unrolled_quantifiers = []

    def get_next_slice_id(self):
        self._slice_counter += 1
        return self._slice_counter

    def set_static_lengths(self, lengths):
        """设置静态长度表；只接受 1..MAX_STATIC_UNROLL 的整数。"""
        self.static_lengths = {
            str(name): int(value)
            for name, value in (lengths or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
            and 1 <= value <= MAX_STATIC_UNROLL
        }
        if self.static_lengths:
            logger.info(f"[Unroll] Static lengths for quantifier unroll : '{self.static_lengths}'")

    def get_static_length(self, var_name):
        """返回可直接用于展开的长度；未知或超上限返回 None（继续用量词）。"""
        return self.static_lengths.get(var_name)

    def set_static_rank_max(self, bounds):
        """设置 shape 维度个数上限表；键为 var_definition 的 ``f"{name}.shape"``，
        只接受 1..MAX_STATIC_UNROLL 的整数。"""
        self.static_rank_max = {
            str(name): int(value)
            for name, value in (bounds or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
            and 1 <= value <= MAX_STATIC_UNROLL
        }
        if self.static_rank_max:
            logger.info(f"[Unroll] Static shape rank bounds for range quantifier : '{self.static_rank_max}'")

    def get_rank_max(self, seq_name):
        """返回 shape seq 的维度个数上限；未知返回 None（继续用量词）。"""
        return self.static_rank_max.get(str(seq_name))

    def set_static_param_ranges(self, ranges):
        """设置标量参数的取值范围表；键为参数名，值为 (lo, hi) 整数二元组。"""
        self.static_param_ranges = {
            str(name): (int(value[0]), int(value[1]))
            for name, value in (ranges or {}).items()
            if isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
            and value[0] <= value[1]
        }
        if self.static_param_ranges:
            logger.info(f"[Unroll] Static scalar ranges for range quantifier : '{self.static_param_ranges}'")

    def get_param_range(self, param_name):
        """返回标量参数的静态取值范围；未知返回 None（继续用量词）。"""
        return self.static_param_ranges.get(str(param_name))

    def assert_param_domain(self, param_name, bounds, assertion):
        """断言 range() 展开依赖的标量区间，同名同区间只断一次。

        这里用 ``assert_and_track`` 而不是 ``solver.add``：展开窗口若因区间过窄
        导致 UNSAT，这条断言必须能在 ``unsat_core()`` 里看到。用 ``add`` 时它没有
        追踪字面量，core 要么是空的、要么指向一组单独看来自洽的条款，排障时会被
        引向错误方向。
        """
        key = (str(param_name), int(bounds[0]), int(bounds[1]))
        if key in self.asserted_param_domains:
            return
        self.asserted_param_domains.add(key)
        self.solver.assert_and_track(
            assertion, f"unroll_domain:{key[0]}[{key[1]},{key[2]}]")

    def record_unroll(self, var_name, kind, length):
        self.unrolled_quantifiers.append({"param": var_name, "kind": kind, "length": length})

    def declare_var(self, var_name, type_hint="scalar", dtype=None, allowed_dtypes=None, allowed_formats=None,
                    range_value=None, length=None, is_print_log=False):
        if var_name in self.var_map:
            logger.warning(f"[Warn] var {var_name} already declared")
            return

        if type_hint not in self._VAR_FACTORY:
            logger.error(f"[Declare] Unsupported type_hint '{type_hint}' for var '{var_name}'")
            return

        kwargs = {
            'dtype': dtype, 'allowed_dtypes': allowed_dtypes,
            'allowed_formats': allowed_formats, 'range_value': range_value,
            'length': length
        }
        cls, param_fn = self._VAR_FACTORY[type_hint]

        if type_hint in ['scalar', 'list'] and not dtype:
            # List 现在可以通过 range_value 推断类型，所以不强制报错，但 scalar 必须有
            if type_hint == 'scalar':
                logger.error(f"[Declare] Type '{type_hint}' requires 'dtype' argument for var '{var_name}'")
                return

        try:
            var_obj = cls(var_name, *param_fn(self, kwargs))
            self.var_map[var_name] = var_obj
            if is_print_log:
                logger.debug(f"[Declare] {var_name} -> {type_hint} (dtype: {dtype}, range_value: {range_value})")
        except Exception as e:
            logger.error(f"[Declare] Failed to create var '{var_name}', kwargs : '{kwargs}', err msg : '{e}'")

    def get_var(self, var_name):
        if var_name not in self.var_map:
            logger.warning("Var not in var map, the parameter was not declared, it's is_present is False")
            return None
        return self.var_map[var_name]

    def add_constraints(self, expr_str_dict: Dict[str, str]):
        for expr_str_name, expr_str in expr_str_dict.items():
            # 通用 TODO 标记：在 apply_keyword_replace 与 validate_expression 之前先
            # 检测前缀，避免 `# TODO:` 被 ast.parse 拒为 invalid syntax。前缀检测
            # 与 add_constraint 内 TODO 分支一致，确保 solver 与 validator 行为统一。
            if isinstance(expr_str, str) and expr_str.lstrip().startswith("# TODO:"):
                self.add_constraint(expr_str_name, expr_str)
                continue
            replace_expr = ExpressionPreprocessor.apply_keyword_replace(expr_str)
            if ExpressionPreprocessor.validate_expression(replace_expr):
                self.add_constraint(expr_str_name, replace_expr)

    def add_constraint(self, expr_name, expr_str, is_print_log=False):
        # 通用规则：约束文本以 `# TODO:` 开头时跳过 solver（Z3 求解不完备或约束语义
        # 需人工 channel 时使用），但仍记录到 dropped_constraints 携带 reason="todo_skip"，
        # PostCheck 区分真实 drop 与 todo_skip，前者阻断、后者放行。Prefix 是文本层标记，
        # 不依赖 AST；任何 family 通用。
        if isinstance(expr_str, str) and expr_str.lstrip().startswith("# TODO:"):
            dropped = {
                "name": expr_name,
                "expr": expr_str,
                "error": "todo_skip",
                "reason": "todo_skip",
            }
            self.dropped_constraints.append(dropped)
            if is_print_log:
                logger.debug(f"[TODO-SKIP] {expr_str}")
            return
        try:
            tree = ast.parse(expr_str, mode='eval')
            converter = ASTtoZ3Converter(self)
            z3_constraint = converter.visit(tree.body)
            if z3_constraint is not None:
                self.solver.assert_and_track(z3_constraint, expr_name)
                # 通用规则：检测到 z3.Or(z3.Bool==False, z3.String!=X) 形式时，附加
                # Implies(X, ==False) 作为 hint。该形式 Z3 对 Or 的传播不完备（mixed-sort：
                # Bool/String），Implies 形式 Z3 watch-list 处理更优，能让 solver 拒绝
                # spurious SAT。等价性：`Or(A==False, B!=X) ⇔ Implies(B==X, A==False)`。
                # Z3 simplify 后节点变换：`A==False` → `Not(A)`；`B!=X` → `Not(B==X)`，
                # 必须同时探测两种表示，否则 hint 加入失败。
                try:
                    if z3.is_or(z3_constraint):
                        children = z3_constraint.children()
                        if len(children) == 2:
                            a, b = children
                            bool_side = None  # a 候选：==False 或 Not(x)
                            str_side = None   # b 候选：!=X 或 Not(x=="X")
                            # 模式 (i)：a 是 ==BoolVal(False)，b 是 !=X
                            if (z3.is_eq(a) and a.num_args() == 2
                                    and z3.is_false(a.arg(1))):
                                bool_side = a
                            elif z3.is_not(a) and z3.is_bool(a.arg(0)):
                                bool_side = z3.Not(a.arg(0))  # Not(x) 表示 x==False 等价
                            # 模式 (ii)：b 是 !=X (Not(x==X))
                            if z3.is_not(b) and z3.is_eq(b.arg(0)):
                                str_side = b
                            if bool_side is not None and str_side is not None:
                                target = str_side.arg(0).arg(0)
                                x_val = str_side.arg(0).arg(1)
                                hint = z3.Implies(
                                    target == x_val,
                                    z3.Not(bool_side.arg(0))
                                )
                                self.solver.assert_and_track(
                                    hint, f"{expr_name}#implies_hint")
                except Exception:
                    pass
                if is_print_log:
                    logger.debug(f"[OK] {expr_str}")
            else:
                if is_print_log:
                    logger.debug(f"[SKIP] '{expr_str}': converter returned None, ignored")
        except Exception as e:
            logger.error(f"[FAIL] expr : '{expr_str}': err msg : '{e}'")
            # 真实转换失败必须 fail-closed：记录到 dropped_constraints（区别于
            # todo_skip），由 solve_z3_constraints 判定真实 drop 并阻断该 case。
            # 禁止静默丢弃约束后继续产出缺约束的"伪 SAT"非法用例（曾致
            # aclnnApplyAdamWQuant absmax 尺寸失控 → NPU aivec 崩溃）。
            self.dropped_constraints.append({
                "name": expr_name,
                "expr": expr_str,
                "error": str(e),
                "reason": "conversion_failed",
            })

    def solve(self):
        if self._timeout_ms:
            self.solver.set('timeout', self._timeout_ms)
        check_res = self.solver.check()
        logger.info(f"Solve result: {check_res}")
        results = {}
        if check_res == z3.sat:
            m = self.solver.model()
            for name, var_obj in self.var_map.items():
                logger.info(f"Param name : {name}")
                results[name] = var_obj.resolve_model(m)
                logger.info(f"  {name}: {results[name]}")
        elif check_res == z3.unknown:
            logger.error(f"Solve result unknown, unknown reason : {self.solver.reason_unknown()}")
        else:
            logger.error("Causes of Constraint Conflicts")
            unsat_cores = self.solver.unsat_core()
            for core in unsat_cores:
                logger.error(f"   {core}   ")
        return results
