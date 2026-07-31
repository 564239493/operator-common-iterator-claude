#!/usr/bin/env bash
# 单算子 fresh-session 运行器。每算子开一个全新 `claude -p` 会话（print 模式：
# 非交互、跑完自动退出），执行 /iterate-operator 全流程（同样经过 Agent、质量门禁、
# 产物校验，不绕过任何环节）。新会话有完整上下文预算，规避单会话跑 4-5 个算子后
# 上下文耗尽导致 /iterate-directory 停摆的问题。
#
# 拆成独立脚本文件（而非内联 `bash -c '...'`）的原因：windowed 模式下 `cmd start`
# 启动新窗口时，内联命令的嵌套引号会被 cmd 破坏。用脚本文件 + 简单位置参数，引号
# 安全，headless 与 windowed 两种模式共用同一运行器。
#
# 参数（均为简单串，无嵌套引号，可安全在新窗口中启动）:
#   $1 operator_doc        算子文档路径（项目相对或绝对）
#   $2 max_iterations      默认 1
#   $3 case_count          默认 10
#   $4 scene               auto|all|off，默认 all（headless 不弹 AskUserQuestion）
#   $5 mode                real|mock，默认 real
#   $6 log_dir             日志目录，写 <stem>.log
#   $7 mark_file           完成后写 "<rc>\t<outcome>\t<state>\t<run_dir>" 供编排器 poll
#   $8.. passthrough       透传给 /iterate-operator 的额外参数（如 --src、--supplement-constraints）
#
# 环境变量:
#   FRESH_PROBE=1   用 /show-workforce 探测 plumbing（不跑真算子，用于自测）
#   FRESH_HOLD=1    claude 退出后 `read` 留窗等回车（windowed 模式人工复核用）
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 子 claude 的 Bash 工具会继承本进程 PATH，故把 venv 前置，确保其 `python` 解析到
# 带 z3/pydantic/asyncssh 的解释器（init_run.py / generate_cases.py 等依赖）。
VENV_BIN="$PROJECT_ROOT/.venv/Scripts"
if [[ -x "$VENV_BIN/python.exe" || -x "$VENV_BIN/python" ]]; then
  export PATH="$VENV_BIN:$PATH"
fi

doc="${1:?operator doc required}"
maxit="${2:-1}"
count="${3:-10}"
scene="${4:-all}"
mode="${5:-real}"
log_dir="${6:-$PROJECT_ROOT/logs/iterate_dir_fresh}"
mark="${7:-}"
# 跳过已读的位置参数，剩余作为透传。
n=$(( $# < 7 ? $# : 7 ))
shift "$n"
extra=("$@")

stem="$(basename "$doc" .md)"

if [[ "${FRESH_PROBE:-0}" == "1" ]]; then
  cmd="/show-workforce"
else
  cmd="/iterate-operator $doc --max-iterations $maxit --case-count $count --scene $scene --mode $mode ${extra[*]}"
fi

mkdir -p "$log_dir"
log="$log_dir/$stem.log"
: > "$log"

echo "[fresh-run] stem=$stem" | tee -a "$log"
echo "[fresh-run] cmd=$cmd" | tee -a "$log"
echo "[fresh-run] started_at=$(date '+%Y-%m-%dT%H:%M:%S')" | tee -a "$log"

# 记录本算子已有的 run 目录快照，事后据此识别本次新建的 run（init_run 失败不建目录）。
before=""
while IFS= read -r line; do before="$before$line "; done < <(ls -d "$PROJECT_ROOT/runs/$stem"-* 2>/dev/null)

claude -p "$cmd" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}

# 找本次新建的 run 目录（不在 before 快照里）。
newest=""
while IFS= read -r d; do
  [[ -f "$d/run_state.json" ]] || continue
  case " $before " in *" $d "*) : ;; *) newest="$d"; break;; esac
done < <(ls -d "$PROJECT_ROOT/runs/$stem"-* 2>/dev/null)

state=""
if [[ -n "$newest" && -f "$newest/run_state.json" ]]; then
  state="$(python -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("state",""))' "$newest/run_state.json" 2>/dev/null || true)"
fi

case "$state" in
  SUCCESS) outcome=success;;
  BLOCKED) outcome=blocked;;
  MAX_ITERATIONS) outcome=max_iterations;;
  STOP_GENERATOR_BUG|STOP_EXECUTOR_BUG) outcome=stop_bug;;
  "") outcome=no_run;;
  *) outcome="other:$state";;
esac

echo "[fresh-run] rc=$rc state=$state outcome=$outcome run=$newest" | tee -a "$log"
echo "[fresh-run] ended_at=$(date '+%Y-%m-%dT%H:%M:%S')" | tee -a "$log"

if [[ -n "$mark" ]]; then
  printf '%s\t%s\t%s\t%s\n' "$rc" "$outcome" "$state" "${newest:-}" > "$mark"
fi

if [[ "${FRESH_HOLD:-0}" == "1" ]]; then
  read -r -p "[fresh-run] $stem done ($outcome). 按回车关闭窗口..." _
fi

exit "$rc"
