#!/usr/bin/env bash
# 目录级 fresh-session 迭代器。逐个算子开全新 `claude -p` 会话跑 /iterate-operator，
# 规避 /iterate-directory 单会话跑 4-5 个算子后上下文耗尽停摆的问题：每个算子独享
# 一个干净 Claude 会话（完整上下文预算），同样经过 Agent、质量门禁、产物校验，
# 不绕过任何环节。
#
# 用法:
#   scripts/iterate_dir_fresh.sh [directory] [options]
#     directory             算子文档目录 (默认 operator_docs)
#     --max-iterations N    默认 1
#     --case-count N        默认 10
#     --scene auto|all|off  默认 all (headless: 不弹 AskUserQuestion；auto 会弹窗仅适合 windowed)
#     --mode real|mock      默认 real
#     --filter GLOB         默认 *.md
#     --only a,b,c          只跑这些算子名(stem)
#     --skip a,b,c          跳过这些
#     --start-from NAME     从该算子开始(含)
#     --force               已有 SUCCESS 终态也重跑 (默认跳过已成功)
#     --skip-terminal       跳过已有任意终态的算子 (默认只跳过 SUCCESS)
#     --fail-fast           首个非 SUCCESS 终态即停 (默认 continue-on-error)
#     --windowed            每算子开新窗口跑 (默认 headless 不开窗)
#     --hold                windowed 下跑完留窗等回车 (默认关窗自动推进)
#     --claude-arg ARG      透传给 /iterate-operator (可重复, 如 --src / --supplement-constraints)
#     --dry-run             只打印将执行的命令, 不跑
#   环境变量:
#     FRESH_PROBE=1   用 /show-workforce 探测 plumbing (不跑真算子, 用于自测)
#     FRESH_PROBE_LIMIT=N  探测时只跑前 N 个算子 (默认 1)
#
# 日志与产物: per-op 日志在 logs/iterate_dir_fresh/<ts>/<stem>.log，汇总写
# logs/iterate_dir_fresh/<ts>/summary.json + status.tsv。runs/ 与 logs/ 均被 .gitignore 忽略。
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$PROJECT_ROOT/scripts/_fresh_run_one.sh"
cd "$PROJECT_ROOT" || exit 2

# ---- defaults ----
directory="operator_docs"
maxit=1; count=10; scene="all"; mode="real"; filter="*.md"
only=""; skip=""; start_from=""
force=0; skip_terminal=0; fail_fast=0
windowed=0; hold=0; dry_run=0
declare -a claude_args=()
declare -a positionals=()

while (( $# )); do
  case "$1" in
    --max-iterations) maxit="$2"; shift 2;;
    --case-count) count="$2"; shift 2;;
    --scene) scene="$2"; shift 2;;
    --mode) mode="$2"; shift 2;;
    --filter) filter="$2"; shift 2;;
    --only) only="$2"; shift 2;;
    --skip) skip="$2"; shift 2;;
    --start-from) start_from="$2"; shift 2;;
    --force) force=1; shift;;
    --skip-terminal) skip_terminal=1; shift;;
    --fail-fast) fail_fast=1; shift;;
    --windowed) windowed=1; shift;;
    --hold) hold=1; shift;;
    --dry-run) dry_run=1; shift;;
    --claude-arg) claude_args+=("$2"); shift 2;;
    -h|--help) sed -n '3,28p' "$0"; exit 0;;
    --) shift; while (( $# )); do positionals+=("$1"); shift; done;;
    -*) echo "未知参数: $1 (用 --help 查看)" >&2; exit 2;;
    *) positionals+=("$1"); shift;;
  esac
done
if (( ${#positionals[@]} )); then directory="${positionals[0]}"; fi

if [[ ! -d "$directory" ]]; then
  echo "错误: 目录不存在: $directory" >&2; exit 2
fi
if [[ ! -f "$RUNNER" ]]; then echo "错误: 缺运行器 $RUNNER" >&2; exit 2; fi

export FRESH_HOLD=$hold

# ---- 枚举算子文档（非递归，与 init_batch 默认一致） ----
mapfile -t docs < <(find "$directory" -maxdepth 1 -type f -name "$filter" | sort)
total=${#docs[@]}
if (( total == 0 )); then
  echo "错误: $directory 下无匹配 $filter 的文档" >&2; exit 2
fi

# ---- 过滤集合 ----
# --only/--skip 用逗号列表 case 匹配（避免 set -u 下空关联数组的 unbound 陷阱）

# ---- 运行器透传参数串（用于 dry-run 展示与 windowed 启动） ----
extra_str=""
if (( ${#claude_args[@]} )); then extra_str="${claude_args[*]}"; fi

# 终态判定：返回某 stem 最新 run_state.json 的 state（无则空串）。
latest_state() {
  local stem="$1" d st=""
  while IFS= read -r d; do
    [[ -f "$d/run_state.json" ]] || continue
    st="$d"
  done < <(ls -dt "$PROJECT_ROOT/runs/$stem"-*/ 2>/dev/null)
  [[ -n "$st" ]] || return 0
  python -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("state",""))' "$st/run_state.json" 2>/dev/null || true
}

is_terminal() { case "$1" in SUCCESS|BLOCKED|MAX_ITERATIONS|STOP_GENERATOR_BUG|STOP_EXECUTOR_BUG) return 0;; *) return 1;; esac; }

# ---- 日志目录 ----
ts="$(date '+%Y%m%d-%H%M%S')"
log_dir="$PROJECT_ROOT/logs/iterate_dir_fresh/$ts"
mkdir -p "$log_dir"
status_tsv="$log_dir/status.tsv"
printf 'stem\toutcome\tstate\trun_dir\tlog\n' > "$status_tsv"

echo "=========================================================="
echo " iterate_dir_fresh  directory=$directory  total=$total"
echo " max-iterations=$maxit case-count=$count scene=$scene mode=$mode"
echo " windowed=$windowed hold=$hold force=$force skip-terminal=$skip_terminal fail-fast=$fail_fast dry-run=$dry_run"
echo " extra=[${extra_str}]  log_dir=$log_dir"
echo "=========================================================="

declare -A counts=([run]=0 [success]=0 [blocked]=0 [max_iterations]=0 [stop_bug]=0 [no_run]=0 [other]=0 [skipped]=0 [timeout]=0)
probe_limit="${FRESH_PROBE_LIMIT:-1}"
probe_n=0
reached_start=0

for doc in "${docs[@]}"; do
  stem="$(basename "$doc" .md)"

  # --start-from：未到目标算子前一律跳过
  if [[ -n "$start_from" ]]; then
    if (( reached_start == 0 )); then
      if [[ "$stem" == "$start_from" ]]; then reached_start=1; else continue; fi
    fi
  fi

  # --only / --skip（逗号列表 case 匹配）
  if [[ -n "$only" ]]; then case ",$only," in *",$stem,"*) ;; *) continue;; esac; fi
  if [[ -n "$skip" ]]; then case ",$skip," in *",$stem,"*) continue;; esac; fi

  # 恢复检查：默认跳过已 SUCCESS；--skip-terminal 跳过任意终态；--force 不跳。
  if (( ! force )); then
    pst="$(latest_state "$stem")"
    if is_terminal "$pst"; then
      if [[ "$pst" == "SUCCESS" ]] || (( skip_terminal )); then
        echo "[$stem] SKIP (已有终态 $pst; --force 可重跑)"
        printf '%s\tskipped(%s)\t%s\t-\t-\n' "$stem" "$pst" "$pst" >> "$status_tsv"
        counts[skipped]=$((counts[skipped]+1)); continue
      fi
    fi
  fi

  # 探测模式限量（只计真正会跑的算子，跳过的不计）
  if [[ "${FRESH_PROBE:-0}" == "1" ]]; then
    (( probe_n >= probe_limit )) && { echo "[probe] 已达 FRESH_PROBE_LIMIT=$probe_limit，停止"; break; }
    probe_n=$((probe_n+1))
  fi

  mark="$log_dir/$stem.mark"
  rm -f "$mark"

  runner_args=("$doc" "$maxit" "$count" "$scene" "$mode" "$log_dir" "$mark" "${claude_args[@]}")

  if (( dry_run )); then
    echo "[$stem] DRY-RUN: bash $RUNNER ${runner_args[*]}"
    printf '%s\tdry-run\t-\t-\t-\n' "$stem" >> "$status_tsv"; continue
  fi

  echo "------ $stem ------"
  if (( windowed )); then
    title="fresh: $stem"
    cmd //c start "$title" bash "$RUNNER" "${runner_args[@]}"
    # poll 等待 mark 文件（每算子独立窗口，跑完关窗并写 mark）
    i=0
    while [[ ! -f "$mark" ]] && (( i < 43200 )); do sleep 2; ((i++)); done
    if [[ -f "$mark" ]]; then
      IFS=$'\t' read -r rc outcome state run_dir < "$mark"
    else
      rc=124 outcome=timeout state="" run_dir=""
    fi
  else
    bash "$RUNNER" "${runner_args[@]}"
    rc=$?
    if [[ -f "$mark" ]]; then
      IFS=$'\t' read -r _ outcome state run_dir < "$mark"
    else
      outcome="no_mark" state="" run_dir=""
    fi
  fi

  printf '%s\t%s\t%s\t%s\t%s\n' "$stem" "$outcome" "$state" "${run_dir:-}" "$log_dir/$stem.log" >> "$status_tsv"
  counts[run]=$((counts[run]+1))
  case "$outcome" in
    success|blocked|max_iterations|stop_bug|no_run|timeout) counts[$outcome]=$((counts[$outcome]+1));;
    other:*) counts[other]=$((counts[other]+1));;
    *) counts[other]=$((counts[other]+1));;
  esac

  if (( fail_fast )); then
    if [[ "$outcome" != "success" ]]; then
      echo "[fail-fast] $stem 终态 $outcome($state)，停止后续算子。"
      break
    fi
  fi
done

# ---- 汇总 ----
echo "=========================================================="
echo " 汇总  ran=${counts[run]}  success=${counts[success]}  blocked=${counts[blocked]}  max_iter=${counts[max_iterations]}  stop_bug=${counts[stop_bug]}  no_run=${counts[no_run]}  other=${counts[other]}  timeout=${counts[timeout]}  skipped=${counts[skipped]}"
echo " status.tsv: $status_tsv"
echo " 日志目录:   $log_dir"
echo "=========================================================="

python - "$status_tsv" "$log_dir/summary.json" <<'PY'
import csv, json, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8"), delimiter="\t"))
summary = {
    "total_enumerated": len(rows),
    "counts": {k: int(v) for k, v in zip(
        ["run","success","blocked","max_iterations","stop_bug","no_run","other","skipped","timeout"],
        [sum(1 for r in rows if r["outcome"] not in ("skipped(SUCCESS)","dry-run","skipped(BLOCKED)","skipped(MAX_ITERATIONS)","skipped(STOP_GENERATOR_BUG)","skipped(STOP_EXECUTOR_BUG)"))] + [0]*8)},
    "rows": rows,
}
# 更精确按 outcome 前缀聚合
agg = {}
for r in rows:
    o = r["outcome"].split("(")[0]
    agg[o] = agg.get(o, 0) + 1
summary["counts"] = agg
json.dump(summary, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(summary["counts"], ensure_ascii=False))
PY
