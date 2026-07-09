#!/usr/bin/env python3
"""从算子源码快照提取确定性约束与错误串证据(未经 LLM 判读的原始事实)。

子模块(--only):
  binary   解析 op_host/config/<platform>/<op>_binary.json -> platform_matrix
           (从路径取 platform, 从 op_list 取 dtype/format/attrs/input/output 名)
  checks   扫 op_host/** 与 _closure/** 的 .cpp/.cc/.h/.hpp 的 OP_TILING_CHECK /
           OP_CHECK / OP_CHECK_IF / CHECK_COND / OP_CHECK_DTYPE_NOT_SUPPORT /
           OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE / OP_LOGE(仅 ACLNN_ERR_PARAM 族) -> raw_checks
           (括号深度切分取条件 + 错误串 + 源码位置 + 所属函数 + 函数内 error_string 线索;
            .h/.hpp 先剥 #define 续行块避免匹配宏定义; 另产 callees/unresolved_calls
            作调用链导航与完整性信号; .cc 主要见于 asc-devkit 自定义算子, 一并扫)
  aclnn    解析 op_api/aclnn_*.cpp 与 aclnn_*.cc 的 GetWorkspaceSize 签名 -> aclnn_interfaces
           (rglob 任意深度 op_api 兼容 ops-transformer 嵌套与 ops-math 平级;
            过滤 aclnnInner 内部接口; 处理一对多: 一个算子目录多个 aclnn 接口)
  delegations 扫 op_host/** 与 _closure/** 的 .cpp/.cc/.h/.hpp 函数体里 l0op::<Symbol>(
           与 CommonOpExecutorRun( 调用点 -> delegated_kernels。l0op::<Symbol> impl 在算子所属
           ops-* 子树内 conversion 族 .cpp, --src-tree 模式由 snapshot_source 声明头驱动拉入
           _closure/delegated/(其 OP_CHECK 进 raw_checks); CommonOpExecutorRun 在 opbase 跨仓库
           框架执行器, 及 impl 内部调的别的 l0op 内核(其 impl 未拉), include-闭包不可达。
           raw_checks 只扫 check 宏条件抓不到函数体调用, 本子模块补确定性信号,
           供 source-analyst 按 MANIFEST.delegated_symbols_resolved 分情况判定(已解析判读+冗余
           过滤, 未解析产 missing_evidence, 不猜测其约束)。
  all      顺序执行以上全部, 输出合并的 source_raw.json (默认)

输出 source_raw.json, 供 source-analyst agent 做 expr_type 归类与约束差异判读。
本脚本只做确定性正则/JSON 提取, 不做语义判读,
遵循 CLAUDE.md "Python 只负责确定性的校验/留痕, 业务推理通过 Skill 与 Agent"。
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

from _cpp_parse import (
    FUNC_RE,
    _CONTROL_FLOW,
    _func_starts,
    _is_definition,
    _matching_brace_end,
    _matching_paren_end,
    _scan_assignments,
    _scan_const_definitions,
)


def parse_binary(snapshot: Path) -> tuple[dict, str]:
    """解析所有平台的 binary.json, 返回 (platform_matrix, operator_name)。

    platform 形如 op_host/config/<platform>/<op>_binary.json, 从路径取平台名。
    每个 op_list 条目是一个 dtype 派发; 聚合该平台支持的 dtype/format/attrs。
    """
    matrix: dict[str, dict] = {}
    operator_name = ""
    config_dir = snapshot / "op_host" / "config"
    if not config_dir.is_dir():
        return matrix, operator_name
    for bj in config_dir.glob("**/*binary.json"):
        platform = bj.parent.name
        try:
            data = json.loads(bj.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not operator_name and data.get("op_type"):
            operator_name = data["op_type"]
        dtypes: list[str] = []
        formats: set[str] = set()
        attrs: dict = {}
        input_names: list[str] = []
        output_names: list[str] = []
        for entry in data.get("op_list", []):
            ins = entry.get("inputs", []) or []
            if ins:
                dt = ins[0].get("dtype")
                if dt and dt not in dtypes:
                    dtypes.append(dt)
            for i in ins:
                if i.get("format"):
                    formats.add(i["format"])
                if i.get("name") and i["name"] not in input_names:
                    input_names.append(i["name"])
            for o in entry.get("outputs", []) or []:
                if o.get("name") and o["name"] not in output_names:
                    output_names.append(o["name"])
            for a in entry.get("attrs", []) or []:
                attrs[a.get("name", "")] = a.get("value")
        matrix[platform] = {
            "dtype": dtypes,
            "format": sorted(formats),
            "attrs": attrs,
            "input_names": input_names,
            "output_names": output_names,
        }
    return matrix, operator_name


# FUNC_RE / _CONTROL_FLOW 已抽至 _cpp_parse.py(与 snapshot_source.py 共用,见顶部 import)。

# 所有约束性宏起始(长前缀优先, 避免裸 OP_CHECK 误吃 OP_CHECK_DTYPE_NOT_SUPPORT/OP_CHECK_IF)。
# 仅匹配宏名与 '(', 参数用括号深度切分(比纯正则稳健, 处理多行/嵌套/相邻字面量拼接)。
# OP_TILING_CHECK(cond, log_func, return_expr) 为 3 参形式, 语义与 OP_CHECK 一致(cond 真→报错),
# 多见于 *_tiling.cpp; \bOP_TILING_CHECK\s*\(' 不会误吃 OP_TILING_CHECK_STATUS_RETURN(...)
# (其后继字符是 '_' 非 '(')。OP_CHECK 非其前缀, 无交替歧义。
# OP_LOGE(errcode, fmt, ...): 无条件日志宏, 非检查宏; 仅收参数族错误码(见 extract_checks
# 内 ACLNN_ERR_PARAM 过滤), 抓 L0 impl 的 Check* 函数参数校验消息。OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE
# (cond, return_stmt, msg) 为 3 参, cond 真→报错(同 OP_CHECK 极性), 经 else 分支处理。
# 长前缀优先: OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE 必须排在裸 OP_CHECK 前, 否则裸 OP_CHECK
# 先吃前缀后遇 '_' 回溯(虽最终能匹配, 排前更稳/更快)。OP_LOGE 与其他无前缀重叠, \bOP_LOGE\s*\(
# 不误吃 OP_LOGW/OP_LOGI/OP_LOGD(末字符不同)。
_CHECK_MACRO_RE = re.compile(
    r'\b(OP_TILING_CHECK|OP_CHECK_DTYPE_NOT_SUPPORT|OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE'
    r'|OP_CHECK_IF|OP_CHECK|OP_LOGE|CHECK_COND)\s*\('
)

# 条件串里识别"被调用名"用: 排除 C++ 关键字/转换算子/内置, 避免把 static_cast/sizeof/std::max
# 当成未解析调用制造噪音。unresolved_calls 仅上报"含大写字母"的疑似用户函数名
# (CheckParamsShape 等), 全小写的 max/abs/size 视为 std/内置不入列。
_CPP_NON_CALL = {
    "if", "for", "while", "switch", "return", "do", "else", "catch", "try",
    "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    "sizeof", "alignof", "alignas", "decltype", "typeof", "new", "delete",
    "throw", "noexcept", "constexpr", "consteval", "operator",
}
_CALLEE_RE = re.compile(r'\b([A-Za-z_]\w*)\s*\(')

# 委派标记: 算子 op_host/impl 函数体里对 level-0 共享内核(l0op:: 命名空间)或通用二段执行器
# (CommonOpExecutorRun)的调用点。l0op::<Symbol> impl 在算子所属 ops-* 子树内 conversion 族
# .cpp(同子树, 经**链接期符号定义**关联而非 #include); --src-tree 模式由 snapshot_source.py
# 声明头驱动拉入 _closure/delegated/(其 OP_CHECK 进 raw_checks), CommonOpExecutorRun 在 opbase
# 跨仓库框架执行器及 impl 内部调的别的 l0op 内核(其 impl 未拉)仍 include-闭包不可达。
# raw_checks._callee_names 只扫 check 宏条件, 抓不到函数体里这类调用, 本正则补确定性信号
# -> delegated_kernels, 供 source-analyst 按 MANIFEST.delegated_symbols_resolved 分情况判定。
# 注: 仅匹配 l0op:: 限定形式; 算子若 `using namespace l0op;` 后裸调 TransData() 不被本正则
# 捕获(裸名歧义大), 属已知残差, 由 source-analyst 读源码时手工补。
_DELEGATION_RE = re.compile(r'\b(?:l0op::([A-Za-z_]\w*)|CommonOpExecutorRun)\s*\(')


# _func_starts 已抽至 _cpp_parse.py(见顶部 import)。


def _extract_paren_args(text: str, open_paren: int) -> str | None:
    """从 '(' 位置取到匹配 ')' 的括号内文本(不含外层括号), 处理字符串/转义/嵌套。未闭合返 None。"""
    depth = 0
    i = open_paren
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return text[open_paren + 1:i]
        i += 1
    return None


def _split_top_args(args: str) -> list[str]:
    """按顶层逗号(深度 0)切分参数列表, 返回去空白后的各参数。字符串内逗号不计。"""
    parts: list[str] = []
    depth = 0
    in_str = False
    esc = False
    cur: list[str] = []
    for c in args:
        if in_str:
            cur.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
                cur.append(c)
            elif c == '(':
                depth += 1
                cur.append(c)
            elif c == ')':
                depth -= 1
                cur.append(c)
            elif c == ',' and depth == 0:
                parts.append(''.join(cur).strip())
                cur = []
            else:
                cur.append(c)
    parts.append(''.join(cur).strip())
    return parts


def _extract_string_literals(s: str) -> str:
    """拼接 s 中所有字符串字面量内容(处理 "a" "b" 相邻拼接), 不还原转义。"""
    return ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', s))


def _line_of(text: str, pos: int) -> int:
    return text[:pos].count('\n') + 1


def _strip_define_blocks(text: str) -> str:
    """把 #define 指令行及其反斜杠续行**整行置空**(保留换行, 不改行号)。

    防止把宏 *定义* (#define OP_TILING_CHECK(cond, log_func, expr) ...) 与包装宏
    (#define MY_CHECK(...) \\n OP_TILING_CHECK(...)) 当成真实 check 用法扫描。
    仅跳 '#' 行不够——续行不以 '#' 开头。置空而非删行, 使 _line_of 给出的源码行号
    与原文件一致, source_location 可被 analyst 直接 Read。
    """
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if re.match(r'^\s*#\s*define\b', line):
            out.append('')
            prev = line
            i += 1
            # 置空所有续行(前一非空行以 '\' 结尾则当前行为续行)
            while i < n and re.search(r'\\\s*$', prev):
                out.append('')
                prev = lines[i]
                i += 1
        else:
            out.append(line)
            i += 1
    return '\n'.join(out)


# _matching_paren_end / _is_definition 已抽至 _cpp_parse.py(见顶部 import)。


def _callee_names(condition: str) -> list[str]:
    """从条件串抽被调用名(Identifier 后跟 '('), 去重保序, 排除 C++ 关键字/转换算子。"""
    seen: set[str] = set()
    names: list[str] = []
    for m in _CALLEE_RE.finditer(condition):
        name = m.group(1)
        if name in _CPP_NON_CALL or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


# 条件串里的标识符(常量名/局部变量名/参数名), 供 resolved_constants/var_bindings 匹配。
# 与 _cpp_parse._IDENT_RE 同义; 本地定义避免跨模块取私有名。
_COND_IDENT_RE = re.compile(r'[A-Za-z_]\w*')


def _extract_signature_params(text: str, fstart: int) -> list[dict]:
    """从函数定义起点 fstart 取签名形参列表, 返回 [{name, type_raw, role_hint?}]。

    name=形参最后一个标识符; type_raw=其前全部(含 const/&/* /:: 限定, 保留原样供 LLM 判
    输入输出); role_hint 按 name 启发式标 input/output/c0(含 c0->c0; in/src 前缀->input;
    out/dst 前缀->output), 仅提示 LLM 可推翻。默认值(= nullptr)截断; void/... 跳过。
    供 raw_checks.signature_params, 让 source-analyst 不 Read 源码即知 outShape 是输出 shape 形参。
    """
    i = fstart
    n = len(text)
    while i < n and text[i] != '(':
        i += 1
    if i >= n:
        return []
    paren_end = _matching_paren_end(text, i)
    if paren_end is None:
        return []
    args = _split_top_args(text[i + 1:paren_end - 1])
    params: list[dict] = []
    for a in args:
        a = a.strip()
        if not a or a == 'void' or a == '...':
            continue
        a_nodef = a.split('=', 1)[0].strip()  # 截默认值(= nullptr / = 0)
        toks = _COND_IDENT_RE.findall(a_nodef)
        if not toks:
            continue
        name = toks[-1]
        idx = a_nodef.rfind(name)
        type_raw = a_nodef[:idx].strip()
        low = name.lower()
        if 'c0' in low:
            role = 'c0'
        elif low.startswith(('in', 'src')):
            role = 'input'
        elif low.startswith(('out', 'dst')):
            role = 'output'
        else:
            role = None
        p: dict = {"name": name, "type_raw": type_raw}
        if role:
            p["role_hint"] = role
        params.append(p)
    return params


def extract_checks(snapshot: Path, soc_branches: list[dict] | None = None) -> tuple[list[dict], dict]:
    """扫 op_host 与 _closure 下所有 .cpp/.cc/.h/.hpp, 抽约束性宏的条件/错误串/所属函数。

    用括号深度切分提取 OP_TILING_CHECK/OP_CHECK/OP_CHECK_IF/CHECK_COND 的参数
    (比纯正则稳健, 处理多行/嵌套/相邻字面量拼接); OP_CHECK_DTYPE_NOT_SUPPORT
    无字符串字面量, 合成 condition=<tensor>.dtype not in <list>。OP_TILING_CHECK
    (cond, log_func, return_expr) 为 3 参: cond=args[0]、错误串取自 log_func(args[1])、
    return_expr(args[2]) 忽略。OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE(cond, return_stmt, msg)
    走 else 分支(cond=args[0], err 取 msg 字面量)。OP_LOGE(errcode, fmt, ...) 为无条件日志宏:
    仅收 args[0] 含 ACLNN_ERR_PARAM 的调用点, condition 留空(条件在外层 if, 不解析控制流),
    err 取 fmt 字面量——主要丰富 function_errors, 抓 L0 impl 的 Check* 函数参数校验消息。
    扫 .h/.hpp 前先 _strip_define_blocks 整行置空 #define
    续行块, 避免匹配宏 *定义* 本身与包装宏内的 OP_TILING_CHECK。
    func_starts 仅取"定义"(后跟 '{', 排除声明与 return foo(); 语句), 使 owning_function
    指向最近外层函数定义(限定名 Class::method); 同源 def_index 按最后 '::' 段索引,
    为每条 check 产 callees(条件里调了且快照内有定义的函数 + 位置) 与 unresolved_calls
    (调了但无定义的疑似用户函数 → 调用链完整性信号, 全小写名视作 std/内置不入列)。
    每条另带 function_errors(所属函数内 error_string 去重, 作"函数自述前提"线索)。
    合并各扩展名后 sorted 去重, 保证跨 OS 确定序。
    """
    checks: list[dict] = []
    op_host = snapshot / "op_host"
    closure = snapshot / "_closure"
    roots = [d for d in (op_host, closure) if d.is_dir()]
    if not roots:
        return checks
    src_files: list[Path] = []
    for root in roots:
        for ext in ("*.cpp", "*.cc", "*.h", "*.hpp"):
            src_files.extend(root.rglob(ext))
    src_files = sorted(set(src_files))

    # 第一遍: 读 + 剥 #define, 缓存 (rel, text, func_starts, func_info); 建 def_index(仅定义)
    # + 全局 const_map(跨文件常量, 供 resolved_constants)+ per-func body_span/sig_params/assigns
    # (供 var_bindings/signature_params)。const_map 在**未剥 #define 的原文**上扫(含 #define int 宏)。
    file_meta: list[tuple[str, str, list[tuple[int, str]], list[tuple[int, str, tuple[int, int] | None, list[dict], list[dict]]]]] = []  # noqa: E501
    def_index: dict[str, list[dict]] = {}
    const_map: dict[str, int] = {}
    ambiguous_constants: list[dict] = []
    for src_file in src_files:
        rel = src_file.relative_to(snapshot).as_posix()
        raw_text = src_file.read_text(encoding="utf-8", errors="replace")
        # 常量在原文上扫(#define 宏 _strip_define_blocks 会置空); 跨文件合并, 首定义为准,
        # 同名异值记 ambiguous_constants 留痕不阻断(供 source-analyst 警觉取值)。
        for cname, cval in _scan_const_definitions(raw_text).items():
            if cname in const_map:
                if const_map[cname] != cval:
                    amb = next((a for a in ambiguous_constants if a["name"] == cname), None)
                    if amb is None:
                        ambiguous_constants.append(
                            {"name": cname, "values": [const_map[cname], cval]})
                    elif cval not in amb["values"]:
                        amb["values"].append(cval)
            else:
                const_map[cname] = cval
        text = _strip_define_blocks(raw_text)
        func_starts = _func_starts(text)
        func_info: list[tuple[int, str, tuple[int, int] | None, list[dict], list[dict]]] = []
        for start, name in func_starts:
            last_seg = name.rsplit("::", 1)[-1]
            def_index.setdefault(last_seg, []).append({
                "qualified_name": name,
                "source_location": f"{rel}:{_line_of(text, start)}",
            })
            body_span = _func_body_span(text, start)
            sig_params = _extract_signature_params(text, start) if body_span else []
            param_names = {p["name"] for p in sig_params if p.get("name")}
            assigns = (
                _scan_assignments(text[body_span[0]:body_span[1]], param_names=param_names)
                if body_span else []
            )
            func_info.append((start, name, body_span, sig_params, assigns))
        file_meta.append((rel, text, func_starts, func_info))

    # 第二遍: 扫 check 宏, 产 callees/unresolved_calls + 变量绑定脚手架
    # (resolved_constants/var_bindings/signature_params, 仅非空时加, 纯增量)。
    for rel, text, func_starts, func_info in file_meta:
        func_pos = [p for p, _ in func_starts]
        file_checks: list[dict] = []
        for m in _CHECK_MACRO_RE.finditer(text):
            macro = m.group(1)
            open_paren = m.end() - 1  # '(' 位置
            line = _line_of(text, m.start())
            idx = bisect.bisect_right(func_pos, m.start()) - 1
            owner = func_starts[idx][1] if idx >= 0 else ""
            args_text = _extract_paren_args(text, open_paren)
            if args_text is None:
                continue
            args = _split_top_args(args_text)
            if macro == "OP_CHECK_DTYPE_NOT_SUPPORT":
                tensor = args[0] if args else ""
                lst = args[1] if len(args) > 1 else ""
                if tensor and lst:
                    cond = f"{tensor}.dtype not in {lst}"
                    err = f"OP_CHECK_DTYPE_NOT_SUPPORT({tensor}, {lst})"
                else:
                    cond = re.sub(r'\s+', ' ', args_text).strip()
                    err = macro
                kind = "OP_CHECK_DTYPE_NOT_SUPPORT"
            elif macro == "OP_TILING_CHECK":
                # OP_TILING_CHECK(cond, log_func, return_expr): cond=args[0],
                # 错误串取自 log_func(args[1]); return_expr(args[2]) 忽略。
                cond = re.sub(r'\s+', ' ', args[0]).strip() if args else ""
                if not cond:
                    continue
                if len(cond) > 1000:
                    cond = cond[:997] + "..."
                err = _extract_string_literals(args[1]) if len(args) > 1 else ""
                kind = "OP_TILING_CHECK"
            elif macro == "OP_LOGE":
                # OP_LOGE(errcode, fmt, ...): 无条件日志宏, 非检查宏; 仅收参数族错误码
                # (args[0] 含 ACLNN_ERR_PARAM) 的调用点。L0 impl 的 Check* 函数用它报参数校验
                # 失败("TransData not support: %s -> %s" 等), 是 OP_CHECK 系之外的补充信号,
                # 丰富 function_errors 供 source-analyst 判读。condition 留空: OP_LOGE 本身无条件,
                # 触发条件在外层 if(本提取器不解析控制流); 仅 PARAM 族降噪避开 runtime/internal。
                code = args[0].strip() if args else ""
                if "ACLNN_ERR_PARAM" not in code:
                    continue
                cond = ""  # 无条件; 外层 if 的条件不在本提取范围
                err = _extract_string_literals(','.join(args[1:])) if len(args) > 1 else ""
                if not err:
                    continue  # OP_LOGE(errcode) 无 fmt, 跳过
                kind = "OP_LOGE"
            else:
                cond = re.sub(r'\s+', ' ', args[0]).strip() if args else ""
                if len(cond) > 1000:
                    cond = cond[:997] + "..."
                err = _extract_string_literals(','.join(args[1:])) if len(args) > 1 else ""
                if not err and macro in ("OP_CHECK_IF", "CHECK_COND"):
                    # 这两类本应有错误串; 无字面量则跳过(保持原行为)
                    continue
                kind = macro
            called = _callee_names(cond)
            callees: list[dict] = []
            unresolved: list[str] = []
            for cname in called:
                defs = def_index.get(cname)
                if defs:
                    callees.append({
                        "name": cname,
                        "source_location": defs[0]["source_location"],
                        "qualified_name": defs[0]["qualified_name"],
                    })
                elif any(ch.isupper() for ch in cname):
                    unresolved.append(cname)
            entry = {
                "kind": kind,
                "source_location": f"{rel}:{line}",
                "condition": cond,
                "error_string": err,
                "owning_function": owner,
                "callees": callees,
                "unresolved_calls": unresolved,
            }
            # 变量绑定脚手架(仅非空时加, 纯增量, 旧消费方不受影响):
            # resolved_constants: condition 里命中全局 const_map 的 token -> 字面值。
            cond_tokens = set(_COND_IDENT_RE.findall(cond)) if cond else set()
            rc = {t: const_map[t] for t in cond_tokens if t in const_map}
            if rc:
                entry["resolved_constants"] = rc
            # var_bindings / signature_params: 取本 check 所属函数的 body_span/sig_params/assigns。
            if idx >= 0:
                _, _, body_span, sig_params, assigns = func_info[idx]
                if sig_params:
                    entry["signature_params"] = sig_params
                if body_span is not None and assigns:
                    assign_by_token = {a["token"]: a for a in assigns}
                    vb: list[dict] = []
                    for tok in cond_tokens:
                        a = assign_by_token.get(tok)
                        if a is not None:
                            vb.append({
                                "token": tok,
                                "rhs": a["rhs"],
                                "rhs_local_vars": a["rhs_local_vars"],
                                "source_location": f"{rel}:{_line_of(text, body_span[0] + a['lhs_offset'])}",
                            })
                    if vb:
                        entry["var_bindings"] = vb
            file_checks.append(entry)
        # 第三遍: 回填 function_errors(按所属函数分组 error_string 去重)
        by_func: dict[str, list[str]] = {}
        for c in file_checks:
            bucket = by_func.setdefault(c["owning_function"], [])
            if c["error_string"] and c["error_string"] not in bucket:
                bucket.append(c["error_string"])
        for c in file_checks:
            c["function_errors"] = by_func.get(c["owning_function"], [])
        checks.extend(file_checks)
    # 回填 soc_scope(平台维度): raw_check 行号落入哪些 soc_branch 的 if 块范围 ->
    # 该约束属该 SoC 平台; 空=通用(所有产品都满足)。source-analyst 据此设 patch 的
    # target_platform(空->common; 经 soc_product_matrix 映射命中->产品名; 未映射->unknown_socnames)。
    if soc_branches:
        by_rel: dict[str, list[tuple[int, int, str]]] = {}
        for b in soc_branches:
            rel_b, _, _ = b["source_location"].rpartition(":")
            by_rel.setdefault(rel_b, []).append(
                (b["if_start_line"], b["if_end_line"], b["soc_token"]))
        for c in checks:
            rel_c, _, line_c_str = c["source_location"].rpartition(":")
            try:
                line_c = int(line_c_str)
            except ValueError:
                c["soc_scope"] = []
                continue
            scope: list[str] = []
            seen: set[str] = set()
            for ifs, ife, tok in by_rel.get(rel_c, []):
                if ifs <= line_c <= ife and tok not in seen:
                    scope.append(tok)
                    seen.add(tok)
            c["soc_scope"] = scope
    else:
        for c in checks:
            c["soc_scope"] = []
    diagnostics = {"const_ambiguities": ambiguous_constants}
    return checks, diagnostics


# SoC 分支标记(平台维度):两类写法。
# 风格A aclrtGetSocName(): 含 aclrtGetSocName 调用的函数体内的 "Ascend..." 字面量
#   (主机侧 aclnn 接口路径, 真实算子 op_host 里较少见)。
# 风格B SocVersion 枚举: SocVersion::ASCEND\w+ / var==ASCEND\w+ / ASCEND\w+==var
#   (device 侧 tiling 主流写法, prompt_flash_attention/dequant_swiglu_quant 等)。
# 每条 SoC 比较点取其外层 if 块范围(经 _matching_brace_end), 供 raw_check.soc_scope 归属:
# raw_check 行号落入 [if_start_line, if_end_line] 则该约束属该 SoC 平台。
_ACLRT_SOCNAME_RE = re.compile(r'aclrtGetSocName\s*\(')
_ASCEND_LITERAL_RE = re.compile(r'"(Ascend\w*)"')
# SocVersion::ASCEND910B / ASCEND910B == var / var == ASCEND910B(裸枚举比较)
_SOCVERSION_ENUM_RE = re.compile(r'SocVersion::(ASCEND\w+)')
_BARE_ENUM_CMP_RE = re.compile(r'(?:[=!]=)\s*(ASCEND\w+)|(ASCEND\w+)\s*[=!]=')
_IF_HEAD_RE = re.compile(r'\bif\s*\(')


def _func_body_span(text: str, fstart: int) -> tuple[int, int] | None:
    """返回函数定义体 (body_open_brace_pos, body_brace_end_pos) 或 None(声明/未闭合)。

    从签名首个 '(' 经 _matching_paren_end 找 ')', 再向后扫(跳 const/noexcept/override/
    -> trailing/= 0 等)到深度0的 '{' (定义体) 或 ';' (声明)。复用 _is_definition 的判定
    但额外返回 body span, 供 extract_soc_branches 限定 aclrtGetSocName 函数体范围。300 字符兜底。
    """
    n = len(text)
    i = fstart
    while i < n and text[i] != '(':
        i += 1
    if i >= n:
        return None
    paren_end = _matching_paren_end(text, i)
    if paren_end is None:
        return None
    j = paren_end
    depth = 0
    in_str = False
    esc = False
    while j < n and j - paren_end <= 300:
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == ';' and depth == 0:
                return None
            elif c == '{' and depth == 0:
                body_end = _matching_brace_end(text, j)
                if body_end is None:
                    return None
                return (j, body_end)
            elif c in '([':
                depth += 1
            elif c in ')]' and depth > 0:
                depth -= 1
        j += 1
    return None


def _collect_if_blocks(text: str, skipped: list[int]) -> list[tuple[int, int]]:
    """收集所有带花括号块的 if 头: (if_pos, brace_end_pos)。无花括号单语句 if 计入 skipped 不收。

    供 _enclosing_if 给 SoC 比较点定位外层 if 块行范围。if(cond) 后首个非空白须为 '{'。
    """
    blocks: list[tuple[int, int]] = []
    for m in _IF_HEAD_RE.finditer(text):
        if_pos = m.start()
        paren_end = _matching_paren_end(text, m.end() - 1)
        if paren_end is None:
            continue
        k = paren_end
        n = len(text)
        while k < n and text[k] in ' \t\n\r':
            k += 1
        if k >= n or text[k] != '{':
            skipped[0] += 1  # 无花括号单语句 if, 跳过(少带 check 宏)
            continue
        brace_end = _matching_brace_end(text, k)
        if brace_end is None:
            continue
        blocks.append((if_pos, brace_end))
    return blocks


def _enclosing_if(if_blocks: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    """返回直接包裹 pos 的最近 if 块 (if_pos, brace_end): if_pos<pos<brace_end 中 if_pos 最大者。
    嵌套 if 取最内层(最近), 给 SoC 比较点定位其直接外层分支范围。"""
    best: tuple[int, int] | None = None
    for if_pos, brace_end in if_blocks:
        if if_pos < pos < brace_end:
            if best is None or if_pos > best[0]:
                best = (if_pos, brace_end)
    return best


def extract_soc_branches(snapshot: Path) -> tuple[list[dict], int]:
    """扫 op_host 与 _closure 下 .cpp/.cc/.h/.hpp, 抽 SoC 分支(平台维度)标记点。

    两类写法(见模块正则注释):
      - 风格A aclrtGetSocName: 含 aclrtGetSocName 调用的函数体内 "Ascend..." 字面量(子型号族)。
      - 风格B SocVersion 枚举: SocVersion::ASCEND\\w+ 与裸 ==ASCEND\\w+ / ASCEND\\w+== 比较。

    每个 SoC 比较点取外层 if 块范围(_matching_brace_end 算 brace_end), 产 soc_branch:
    {soc_token, match_mode("string_literal"/"enum"), source_location, owning_function,
    if_start_line, if_end_line}。无花括号单语句 if 跳过(计 skipped_bare_if, 留痕)。
    供 extract_checks 给每条 raw_check 回填 soc_scope(行号落入 [if_start_line, if_end_line]
    的 soc_token 去重列表, 空=通用约束)。source-analyst 据 soc_scope 设 patch 的 target_platform
    (经 soc_product_matrix 映射; 空则 common; 未映射落 unknown_socnames)。

    .h/.hpp 一并扫(覆盖头内 inline 函数的 SoC 分支), 扫前 _strip_define_blocks 防 #define 噪音。
    返回 (soc_branches, skipped_bare_if)。按 (source_location, if_start_line, soc_token) 排序确定序。
    """
    branches: list[dict] = []
    skipped_bare_if = 0
    op_host = snapshot / "op_host"
    closure = snapshot / "_closure"
    roots = [d for d in (op_host, closure) if d.is_dir()]
    if not roots:
        return branches, skipped_bare_if
    src_files: list[Path] = []
    for root in roots:
        for ext in ("*.cpp", "*.cc", "*.h", "*.hpp"):
            src_files.extend(root.rglob(ext))
    src_files = sorted(set(src_files))
    for src_file in src_files:
        rel = src_file.relative_to(snapshot).as_posix()
        text = _strip_define_blocks(src_file.read_text(encoding="utf-8", errors="replace"))
        func_starts = _func_starts(text)
        func_pos = [p for p, _ in func_starts]
        # 函数体 span(风格A 限定 aclrtGetSocName 所在函数体范围)
        body_spans: list[tuple[int, int, str]] = []  # (body_open, body_end, owner_name)
        for fstart, fname in func_starts:
            span = _func_body_span(text, fstart)
            if span is not None:
                body_spans.append((span[0], span[1], fname))
        skipped = [0]
        if_blocks = _collect_if_blocks(text, skipped)
        skipped_bare_if += skipped[0]
        # 风格A: 含 aclrtGetSocName 的函数体 -> 该体内 "Ascend..." 字面量
        aclrt_funcs: list[tuple[int, int, str]] = []  # (body_open, body_end, owner)
        for m in _ACLRT_SOCNAME_RE.finditer(text):
            idx = bisect.bisect_right(func_pos, m.start()) - 1
            owner = func_starts[idx][1] if idx >= 0 else ""
            for body_open, body_end, fname in body_spans:
                if body_open <= m.start() < body_end and fname == owner:
                    aclrt_funcs.append((body_open, body_end, owner))
                    break
        seen_a: set[tuple[str, int]] = set()
        for body_open, body_end, owner in aclrt_funcs:
            body_text = text[body_open:body_end]
            for lm in _ASCEND_LITERAL_RE.finditer(body_text):
                tok = lm.group(1)
                abs_pos = body_open + lm.start(1)
                key = (tok, abs_pos)
                if key in seen_a:
                    continue
                seen_a.add(key)
                enc = _enclosing_if(if_blocks, abs_pos)
                if enc is None:
                    continue  # 字面量不在 if 块内(可能是日志串), 不作分支
                branches.append({
                    "soc_token": tok,
                    "match_mode": "string_literal",
                    "source_location": f"{rel}:{_line_of(text, abs_pos)}",
                    "owning_function": owner,
                    "if_start_line": _line_of(text, enc[0]),
                    "if_end_line": _line_of(text, enc[1]),
                })
        # 风格B: SocVersion::ASCEND\w+ 与裸枚举比较
        seen_b: set[tuple[str, int]] = set()
        enum_positions: list[tuple[int, str]] = []
        for m in _SOCVERSION_ENUM_RE.finditer(text):
            enum_positions.append((m.start(1), m.group(1)))
        for m in _BARE_ENUM_CMP_RE.finditer(text):
            tok = m.group(1) or m.group(2)
            if tok:
                start = m.start(1) if m.group(1) else m.start(2)
                enum_positions.append((start, tok))
        for start, tok in enum_positions:
            key = (tok, start)
            if key in seen_b:
                continue
            seen_b.add(key)
            idx = bisect.bisect_right(func_pos, start) - 1
            owner = func_starts[idx][1] if idx >= 0 else ""
            enc = _enclosing_if(if_blocks, start)
            if enc is None:
                continue  # 枚举比较不在 if 块内(可能是赋值/初始化), 不作分支
            branches.append({
                "soc_token": tok,
                "match_mode": "enum",
                "source_location": f"{rel}:{_line_of(text, start)}",
                "owning_function": owner,
                "if_start_line": _line_of(text, enc[0]),
                "if_end_line": _line_of(text, enc[1]),
            })
    branches.sort(key=lambda b: (b["source_location"], b["if_start_line"], b["soc_token"]))
    return branches, skipped_bare_if


def extract_delegations(snapshot: Path) -> list[dict]:
    """扫 op_host 与 _closure 下 .cpp/.cc/.h/.hpp 函数体, 抽 level-0/共享执行器委派调用点。

    检测 `l0op::<Symbol>(` 与 `CommonOpExecutorRun(` 两类标记。l0op::<Symbol>(TransData/
    ViewCopy/Contiguous/Reshape/ReFormat...)impl 在算子所属 ops-* 子树内 conversion 族 .cpp;
    --src-tree 模式由 snapshot_source 声明头驱动拉入 _closure/delegated/(其 OP_CHECK 进
    raw_checks)。CommonOpExecutorRun 在 opbase 跨仓库框架执行器, 及 impl 内部调的别的 l0op
    内核(其 impl 未拉), include-闭包不可达。raw_checks 的 callee 解析只扫 check 宏条件, 抓不到
    函数体里这类调用, 本函数补确定性信号 -> source_raw 的 `delegated_kernels`, 供 source-analyst
    按 MANIFEST.delegated_symbols_resolved 分情况判定(已解析判读+冗余过滤, 未解析产
    missing_evidence, 不猜测其约束)。

    每条 {symbol, source_location, owning_function}; 按 (symbol, source_location) 去重,
    sorted 确定序。owning_function 复用 _func_starts 标最近外层函数定义。.h/.hpp 一并扫
    (覆盖头内 inline 定义里的委派), 扫前 _strip_define_blocks 防 #define 噪音。
    """
    delegations: list[dict] = []
    op_host = snapshot / "op_host"
    closure = snapshot / "_closure"
    roots = [d for d in (op_host, closure) if d.is_dir()]
    if not roots:
        return delegations
    src_files: list[Path] = []
    for root in roots:
        for ext in ("*.cpp", "*.cc", "*.h", "*.hpp"):
            src_files.extend(root.rglob(ext))
    src_files = sorted(set(src_files))
    seen: set[tuple[str, str]] = set()
    for src_file in src_files:
        rel = src_file.relative_to(snapshot).as_posix()
        text = _strip_define_blocks(
            src_file.read_text(encoding="utf-8", errors="replace")
        )
        func_starts = _func_starts(text)
        func_pos = [p for p, _ in func_starts]
        for m in _DELEGATION_RE.finditer(text):
            symbol = m.group(1) if m.group(1) else "CommonOpExecutorRun"
            line = _line_of(text, m.start())
            loc = f"{rel}:{line}"
            key = (symbol, loc)
            if key in seen:
                continue
            seen.add(key)
            idx = bisect.bisect_right(func_pos, m.start()) - 1
            owner = func_starts[idx][1] if idx >= 0 else ""
            delegations.append({
                "symbol": symbol,
                "source_location": loc,
                "owning_function": owner,
            })
    delegations.sort(key=lambda d: (d["source_location"], d["symbol"]))
    return delegations


# aclnnStatus aclnnXxxGetWorkspaceSize(...)  (过滤 aclnnInner 内部接口)
ACLNN_RE = re.compile(r'aclnnStatus\s+(aclnn(?!Inner)\w*GetWorkspaceSize)\s*\(')


def parse_aclnn(snapshot: Path) -> list[str]:
    """解析 op_api/aclnn_*.cpp 的 GetWorkspaceSize 签名, 返回 aclnn 接口名列表。

    op_api 布局因算子来源而异: ops-transformer 嵌套在 op_host/op_api/, ops-math 与
    op_host 平级在 <op>/op_api/。用 rglob 任意深度的 op_api 目录兼容两者。
    扫 .cpp 与 .cc(.cc 主要见于自定义算子)。
    """
    interfaces: list[str] = []
    api_files: list[Path] = []
    for api_dir in snapshot.rglob("op_api"):
        if api_dir.is_dir():
            api_files.extend(api_dir.glob("aclnn_*.cpp"))
            api_files.extend(api_dir.glob("aclnn_*.cc"))
    for cpp in sorted(api_files):
        text = cpp.read_text(encoding="utf-8", errors="replace")
        for m in ACLNN_RE.finditer(text):
            name = m.group(1)
            if name not in interfaces:
                interfaces.append(name)
    return sorted(interfaces)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从算子源码快照提取确定性约束与错误串(source_raw.json)。"
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="src_snapshot 目录路径(如 runs/<run>/inputs/src_snapshot)。",
    )
    parser.add_argument(
        "--only",
        choices=("all", "binary", "checks", "aclnn", "delegations", "soc"),
        default="all",
        help="只提取某一类(默认 all, 输出合并的 source_raw.json)。soc=SoC 分支(aclrtGetSocName"
             "/SocVersion 枚举, 平台维度)。",
    )
    parser.add_argument("--out", default=None, help="输出文件路径(默认 stdout)。")
    args = parser.parse_args()

    snapshot = Path(args.snapshot)
    if not snapshot.is_dir():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "SNAPSHOT_NOT_FOUND",
                "message": "src_snapshot 目录不存在。",
                "snapshot": str(snapshot),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 2

    result: dict = {"src_snapshot": str(snapshot)}
    # soc_branches 为 raw_checks 的 soc_scope 回填所需, checks/soc/all 模式都先算
    soc_branches: list[dict] = []
    soc_skipped = 0
    if args.only in ("all", "soc", "checks"):
        soc_branches, soc_skipped = extract_soc_branches(snapshot)
    if args.only in ("all", "binary"):
        matrix, opname = parse_binary(snapshot)
        result["platform_matrix"] = matrix
        if opname:
            result["operator_name"] = opname
    if args.only in ("all", "aclnn"):
        result["aclnn_interfaces"] = parse_aclnn(snapshot)
    if args.only in ("all", "checks"):
        checks, diag = extract_checks(snapshot, soc_branches=soc_branches)
        result["raw_checks"] = checks
        if diag.get("const_ambiguities"):
            result["const_ambiguities"] = diag["const_ambiguities"]
    if args.only in ("all", "delegations"):
        result["delegated_kernels"] = extract_delegations(snapshot)
    if args.only in ("all", "soc"):
        result["soc_branches"] = soc_branches
        result["soc_skipped_bare_if"] = soc_skipped

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        summary = {
            "ok": True,
            "out": args.out,
            "operator_name": result.get("operator_name", ""),
            "platforms": list(result.get("platform_matrix", {}).keys()),
            "aclnn_interfaces": result.get("aclnn_interfaces", []),
            "raw_checks_count": len(result.get("raw_checks", [])),
            "delegated_kernels_count": len(result.get("delegated_kernels", [])),
            "soc_branches_count": len(soc_branches),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
