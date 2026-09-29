// opencode 原生写入守卫：受保护路径只读 + 活动 run 写隔离 + 跨 run 访问禁止
// + shell 层改文件命令的启发式拦截。规则移植自原 .claude/hooks/guard_project_writes.py。
// 设计约定：
//   - 只观察工具入参并按需阻断；不修改任何 args/输出。
//   - deny → throw（阻断并给出原因）；静默返回 = 放行。
//   - 「需要用户确认」类判断不在 tool.execute.before 做（throw 实现的 ask 没有
//     批准通道，等于永久拒绝）：高风险命令的询问由 opencode.json 静态 ask 规则
//     承担（原生确认框，用户批准后可执行）；本插件在 permission.ask 钩子里对
//     进入询问流程的命令做硬规则复查（命中改判 deny，其余保持询问）。
//   - 记账归一：子代理会话（task 派生，sessionID 各自独立）先沿 parentID 链归一到
//     根会话，主会话与其全部子代理共享同一份 run 绑定；父级链查询失败时退化为
//     按会话各自记账（与不接入本机制时行为一致）。
//   - fail-closed（故障即关门）：插件自身任何异常一律转 ask，绝不静默放行。
//     工程术语，与 fail-open（故障即开门）相对：防线自身失效时默认拒绝，
//     宁可误拦，不可漏放。

import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { isAbsolute, join, normalize, relative, resolve } from "node:path"

const PROTECTED_WRITE_DIRS = ["executer", "agent/generators", ".git"]
const PROTECTED_WRITE_FILES = ["servers.json"]

const TERMINAL_STATES = new Set([
  "SUCCESS",
  "BLOCKED",
  "MAX_ITERATIONS",
  "STOP_GENERATOR_BUG",
  "STOP_EXECUTOR_BUG",
  "STOPPED_BY_USER",
])

const FILE_READ_TOOLS = new Set(["read", "glob", "grep", "list"])
const FILE_WRITE_TOOLS = new Set(["edit", "write", "apply_patch"])

const GENERATION_PROGRESS_REFERENCE = /generation_progress\.py/i
const COMPLEX_GENERATION_MONITOR =
  /\$\(|(?:^|\s)(?:while|case|sleep|grep|head|tail|ps)(?:\s|$)|(?:^|\s)(?:cd|set)\s|(?:^|\s)[A-Za-z_][A-Za-z0-9_]*=|\|/

// shell 层「绕过文件工具直接改文件」的命令（sed -i / perl -pi / git apply / tar 解压等）。
// 旧架构靠 OS sandbox 在内核层强制，opencode 无对应配置位，这里按启发式补位：
// 命中后与 rm/mv 走同一套写入目标检查（受保护路径 / run 隔离 / 项目外路径）。
const SHELL_FILE_MUTATORS =
  /(?<![\w-])(?:sed\s(?:[^;&|]*\s)?--?(?:i\b|in-place\b|inplace\b)|perl\s(?:[^;&|]*\s)?--?(?:pi\b|ip\b|i\b)|g?awk\s[^;&|]*\s-i\s+inplace|dd\s[^;&|]*\bof=|install\s|rsync\s|patch\s|truncate\s|ed\s|git\s+apply\b|git\s+restore\b|tar\s+(?:-(?:[a-zA-Z]*x[a-zA-Z]*\b|--extract\b)|x[a-z]*f\b)|unzip\s[^;&|]*\s(?:-d\b|--directory\b)|find\s[^;&|]*\s-delete\b)(?![\w-])/i

// 环境变量文件禁止 shell 直接读取（对齐静态 read deny；.env.example 豁免）。
const ENV_FILE_REFERENCE =
  /(?:^|[\s"'=:(])\.env(?!\.example\b)(?:\.\w+)?\b/

const WRITE_OR_DELETE =
  /(?<![\w-])(remove-item|del(?:ete)?|erase|rm|rmdir|move-item|move|mv|copy-item|copy|cp|set-content|add-content|out-file|tee|new-item|mkdir|touch)(?![\w-])/i

const EXTERNAL_PATH =
  /"(?<double>[^"]+)"|'(?<single>[^']+)'|(?<bare>[A-Za-z]:[\\/][^\s;&|<>]+|\\\\[^\s;&|<>]+|\.\.[\\/][^\s;&|<>]+|~[\\/][^\s;&|<>]+|(?<![\w.])\/[^\s;&|<>]+)/

const REDIRECTION =
  /(?:^|[\s\d])(?:>>?|2>>?)\s*(?:"(?<double>[^"]+)"|'(?<single>[^']+)'|(?<bare>[^\s;&|]+))/

const RUN_REFERENCE =
  /(?:^|[\s"'=:(\\/])(?:\.\/|\.\\)?runs[\\/](?!batches(?:[\\/]|$))(?<run_id>[^\\/\s"';&|,)]+)/gi

const RUN_TRAVERSAL =
  /(?:^|[\s"'=:(\\/])(?:\.\/|\.\\)?runs[\\/][^\\/\s"';&|,)]+[\\/]\.\.(?:[\\/]|$)/i

const PROTECTED_SHELL_REFERENCE =
  /(?<![\w.-])(?:executer|agent\/generators|servers\.json|\.git)(?=$|[/\s"';&|,)])/i

const INLINE_PYTHON =
  /(?<![\w.-])(?:(?:python(?:3(?:\.\d+)?)?|python\.exe|pythonw(?:\.exe)?|py)(?:\s+[^\s;&|]+)*\s+-(?:c(?:\s|$)|(?:\s|$))|(?:node|perl|ruby)\s+(?:--eval\s+|-e(?:\s|$)))/i

// Python 仅当它是 shell 段首的可执行 token 时才算执行（罗列路径不算）。
const PYTHON_COMMAND =
  /(?:^|&&|\|\||[;|\n])\s*(?:"[^"]*[\\/]python(?:3(?:\.\d+)?)?(?:\.exe)?"|'[^']*[\\/]python(?:3(?:\.\d+)?)?(?:\.exe)?'|[^\s;&|]*(?:python(?:3(?:\.\d+)?)?|python\.exe|pythonw(?:\.exe)?|py))(?=\s)/

const DESTRUCTIVE_COMMAND =
  /(?<![\w-])(?:remove-item|del(?:ete)?|erase|rm|rmdir|move-item|move|mv)(?![\w-])/i

const HIGH_RISK_PATTERNS = [
  [
    /(?<![\w-])(?:pip3?\s+(?:install|uninstall)|python3?(?:\.exe)?\s+-m\s+(?:pip|venv)|uv\s+(?:add|remove|sync|pip)|npm\s+(?:install|uninstall)|apt(?:-get)?\s|dnf\s|yum\s|winget\s|choco\s)/i,
    "依赖或环境变更",
  ],
  [
    /(?:^|&&|\|\||[;|\n])\s*(?:source|eval|invoke-expression|iex|bash\s+-c|sh\s+-c)(?=\s|$)|(?<![\w-])powershell(?:\.exe)?\s+(?:-encodedcommand\b|-command\s+["']?\s*(?:invoke-expression|iex)\b)/i,
    "Shell 求值",
  ],
  [/(?<![\w-])(?:curl|wget)(?![\w-])/i, "外部内容下载"],
  [
    /(?<![\w-])git\s+(?:add|commit|push|pull|merge|rebase|reset|checkout|switch|clean|stash|tag)(?=\s|$)/i,
    "Git 状态变更",
  ],
  [
    /(?<![\w-])(?:sudo|su|chmod|chown|kill|killall|pkill|taskkill|stop-process|setx|reg\s+(?:add|delete))(?![\w-])/i,
    "系统或进程变更",
  ],
]

const TOKEN_RE = /^\s*(?:"(?<double>[^"]+)"|'(?<single>[^']+)'|(?<bare>[^\s;&|]+))/
const INTERPRETER_NAME = /^(?:python3(?:\.\d+)?|python|pythonw|py)(?:\.exe)?$/
const PY_NOARG_SWITCH = /^-[bBdEIPqSsUuVvx34Oo]+$/

function globalize(re) {
  return re.flags.includes("g") ? re : new RegExp(re.source, re.flags + "g")
}

function quotedSpans(text) {
  const spans = []
  let i = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch !== '"' && ch !== "'") {
      i += 1
      continue
    }
    const quote = ch
    const start = i
    i += 1
    while (i < n) {
      if (text[i] === "\\") {
        i += 2
        continue
      }
      if (text[i] === quote) break
      i += 1
    }
    spans.push([start, i < n ? i + 1 : n])
    i += 1
  }
  return spans
}

function nativePathText(text) {
  const expanded = expandVars(expandHome(String(text).trim()))
  if (process.platform === "win32") {
    const m = /^\/([A-Za-z])(?:\/(.*))?$/.exec(expanded)
    if (m) return `${m[1].toUpperCase()}:/${m[2] || ""}`
  }
  return expanded
}

function expandHome(text) {
  if (text === "~") return homedir()
  if (text.startsWith("~/") || text.startsWith("~\\")) {
    return join(homedir(), text.slice(2))
  }
  return text
}

// 对齐 os.path.expandvars：$VAR / ${VAR}（全平台）+ %VAR%（仅 Windows）。
// 未知变量原样保留，与 py 版语义一致。
function expandVars(text) {
  if (process.platform === "win32") {
    text = text.replace(/%([^%\s]+)%/g, (orig, name) =>
      process.env[name] !== undefined ? process.env[name] : orig,
    )
  }
  return text.replace(/\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (orig, braced, plain) => {
    const name = braced !== undefined ? braced : plain
    return process.env[name] !== undefined ? process.env[name] : orig
  })
}

function resolvedPath(text, root) {
  const expanded = nativePathText(text)
  const candidate = isAbsolute(expanded) ? expanded : join(root, expanded)
  return normalize(candidate)
}

function isInside(text, root) {
  let rel = ""
  try {
    rel = relative(root, resolvedPath(text, root))
  } catch {
    return false
  }
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel))
}

function relativeParts(path, root) {
  let rel = ""
  try {
    rel = relative(root, path)
  } catch {
    return null
  }
  if (rel === "") return []
  if (rel.startsWith("..") || isAbsolute(rel)) return null
  return rel.split(/[\\/]/)
}

function runIdForPath(path, root) {
  const parts = relativeParts(path, root)
  if (!parts || parts.length < 2) return null
  if (parts[0].toLowerCase() !== "runs") return null
  if (parts[1].toLowerCase() === "batches") return null
  return parts[1]
}

function runKey(runId) {
  return process.platform === "win32" ? runId.toLowerCase() : runId
}

function isRunsContainer(path, root) {
  const parts = relativeParts(path, root)
  if (parts === null) return false
  if (parts.length === 0) return true
  return parts.length === 1 && parts[0].toLowerCase() === "runs"
}

function isProtectedWrite(path, root) {
  const parts = relativeParts(path, root)
  if (!parts) return false
  const lowered = parts.map((p) => p.toLowerCase())
  if (lowered.length >= 1 && PROTECTED_WRITE_FILES.includes(lowered[0])) return true
  const joined = lowered.join("/")
  return PROTECTED_WRITE_DIRS.some(
    (name) => joined === name || joined.startsWith(name + "/")
  )
}

function isNullSink(text) {
  const normalized = String(text).trim().replace(/^["']|["']$/g, "").replace(/\\/g, "/").toLowerCase()
  return ["/dev/null", "nul", "nul:", "$null"].includes(normalized)
}

function extractedPaths(text, pattern) {
  const paths = []
  for (const match of text.matchAll(globalize(pattern))) {
    const groups = match.groups || {}
    const value = groups.double || groups.single || groups.bare
    if (value) paths.push(value.replace(/[),]+$/, ""))
  }
  return paths
}

function validRunId(runId) {
  return (
    Boolean(runId) &&
    !["runs", "batches"].includes(runId.toLowerCase()) &&
    ![/[/\\]/, /\x00/, /[*?[\]]/].some((re) => re.test(runId))
  )
}

// 子会话 → 根会话 的解析缓存（会话的父子关系创建后不变，进程内记一次即可）。
const scopeRootCache = new Map()

async function resolveScopeKey(client, sessionID) {
  const self = String(sessionID || "unknown")
  if (!client || typeof client.session?.get !== "function") return self
  if (scopeRootCache.has(self)) return scopeRootCache.get(self)
  let key = self
  try {
    const seen = new Set([self])
    let current = self
    for (;;) {
      const raw = await client.session.get({ path: { id: current } })
      const info = raw && raw.data ? raw.data : raw
      const parent = info ? String(info.parentID || info.parent_id || "") : ""
      if (!parent || seen.has(parent)) break
      seen.add(parent)
      current = parent
      key = parent
    }
  } catch {
    // fail-soft：父级链查询失败时按当前会话记账，不因查询故障让守卫罢工。
  }
  scopeRootCache.set(self, key)
  return key
}

export class Scope {
  constructor(root, sessionID) {
    this.root = root
    const safe = String(sessionID || "unknown").replace(/[^A-Za-z0-9_.-]/g, "_")
    this.file = join(root, ".opencode", "runtime", "task_scopes", `${safe}.json`)
  }

  read() {
    try {
      const value = JSON.parse(readFileSync(this.file, "utf-8"))
      const runId = String(value.run_id)
      if (!validRunId(runId)) return null
      if (this.runIsTerminal(runId)) {
        try {
          unlinkSync(this.file)
        } catch {}
        return null
      }
      return runId
    } catch {
      return null
    }
  }

  write(runId) {
    try {
      mkdirSync(join(this.file, ".."), { recursive: true })
      const temp = this.file + ".tmp"
      writeFileSync(temp, JSON.stringify({ run_id: runId }) + "\n", "utf-8")
      renameSync(temp, this.file)
    } catch {}
  }

  runIsTerminal(runId) {
    try {
      const state = JSON.parse(
        readFileSync(join(this.root, "runs", runId, "run_state.json"), "utf-8")
      )
      return TERMINAL_STATES.has(state.state)
    } catch {
      return false
    }
  }

  bind(runId) {
    const current = this.read()
    if (current && runKey(current) === runKey(runId)) return { ok: true }
    if (current && !this.runIsTerminal(current)) {
      return { ok: false, reason: `当前任务已绑定 runs/${current}，禁止切换或访问 runs/${runId}` }
    }
    this.write(runId)
    return { ok: true }
  }
}

function toolPaths(tool, args, root) {
  if (!args) return []
  if (tool === "read" || tool === "edit" || tool === "write") {
    return args.filePath ? [resolvedPath(args.filePath, root)] : []
  }
  if (tool === "apply_patch") {
    // opencode 真实入参是单个 patchText 字符串（*** Begin Patch / *** Add File: 等标记行）。
    // 逐一提取所有涉及路径送检：Add/Update/Delete File 是写入目标；
    // Move 场景里 Update File 的原路径（被移走）与 Move to 的新路径都算写入目标。
    const targets = []
    const text = typeof args.patchText === "string" ? args.patchText : ""
    for (const m of text.matchAll(/^\*\*\*\s+(?:Add|Update|Delete) File: (.+)$/gm)) {
      if (m[1].trim()) targets.push(resolvedPath(m[1].trim(), root))
    }
    for (const m of text.matchAll(/^\*\*\*\s+Move to: (.+)$/gm)) {
      if (m[1].trim()) targets.push(resolvedPath(m[1].trim(), root))
    }
    // 兼容补丁数组形态（file/filePath 字段），两种入参都逐目标送检。
    const patches = Array.isArray(args.patches) ? args.patches : []
    for (const p of patches) {
      const f = p && (p.file || p.filePath)
      if (f) targets.push(resolvedPath(f, root))
    }
    return targets
  }
  if (tool === "glob" || tool === "grep" || tool === "list") {
    return [resolvedPath(args.path || root, root)]
  }
  return []
}

function guardFile(tool, args, root, scope) {
  const paths = toolPaths(tool, args, root)
  const current = scope.read()
  const isWrite = FILE_WRITE_TOOLS.has(tool)

  for (const path of paths) {
    if (isWrite && isProtectedWrite(path, root)) {
      return `受保护路径只允许读取/执行，禁止修改: ${path}`
    }
    const candidate = runIdForPath(path, root)
    if (current) {
      if (isRunsContainer(path, root) && FILE_READ_TOOLS.has(tool)) {
        return (
          `当前任务只允许读取 runs/${current}；` +
          "请把 Glob/Grep 的 path 缩小到当前任务或非 runs 目录"
        )
      }
      if (candidate && runKey(candidate) !== runKey(current)) {
        return `当前任务只允许访问 runs/${current}，禁止访问 runs/${candidate}`
      }
      if (isWrite && (candidate === null || runKey(candidate) !== runKey(current))) {
        return `任务执行期间只允许写入 runs/${current}: ${path}`
      }
    } else if (candidate) {
      const bound = scope.bind(candidate)
      if (!bound.ok) return bound.reason
    }
  }
  return null
}

function isInterpreterToken(token) {
  const name = token.replace(/\\/g, "/").split("/").pop().toLowerCase()
  return INTERPRETER_NAME.test(name)
}

function pythonEntryToken(segment) {
  let pos = 0
  while (pos < segment.length) {
    const m = TOKEN_RE.exec(segment.slice(pos))
    if (!m || m.index !== 0) return null
    const tok = m.groups.double || m.groups.single || m.groups.bare
    if (tok === undefined || tok === null) return null
    pos += m[0].length
    if (isInterpreterToken(tok)) continue
    if (PY_NOARG_SWITCH.test(tok)) continue
    if (tok.startsWith("-")) return null
    return tok
  }
  return null
}

function allPythonEntriesAreProjectFiles(command, root) {
  const matches = [...command.matchAll(globalize(PYTHON_COMMAND))]
  if (matches.length === 0) return true
  for (const match of matches) {
    const segment = command
      .slice(match.index + match[0].length)
      .split(/&&|\|\||[;&|\n]/)[0]
    const entry = pythonEntryToken(segment)
    if (entry === null) return false
    if (!entry.toLowerCase().endsWith(".py") || !isInside(entry, root)) return false
  }
  return true
}

export function guardShell(command, root, scope) {
  const current = scope.read()

  if (GENERATION_PROGRESS_REFERENCE.test(command) && COMPLEX_GENERATION_MONITOR.test(command)) {
    return (
      "禁止用 shell 变量/管道/while/grep/sleep 包装 generation_progress.py；" +
      "请直接前台运行单条绝对路径命令: " +
      "<python> <repo>/scripts/generation_progress.py watch --output-dir <iter> --interval 60"
    )
  }

  if (INLINE_PYTHON.test(command)) {
    return (
      "禁止 python -c、node -e、perl -e 等内联代码；" +
      "请运行现有 scripts/*.py，或使用 read/glob/grep 检查文件"
    )
  }

  for (const match of command.matchAll(globalize(ENV_FILE_REFERENCE))) {
    const dotIndex = match.index + match[0].indexOf(".env")
    if (quotedSpans(command).some(([start, end]) => dotIndex > start && dotIndex < end)) continue
    return "环境变量文件禁止 shell 直接读取（.env / .env.*；.env.example 除外）"
  }

  if (RUN_TRAVERSAL.test(command)) {
    return "禁止通过 runs/<run-id>/../ 跨任务访问"
  }

  const referencedRuns = new Set()
  for (const match of command.matchAll(RUN_REFERENCE)) {
    referencedRuns.add(match.groups.run_id)
  }
  const referencedKeys = new Map([...referencedRuns].map((id) => [runKey(id), id]))
  if (!current && referencedKeys.size > 1) {
    return `单条命令禁止访问多个任务: ${[...referencedRuns].sort()}`
  }
  let bound = current
  if (!current && referencedKeys.size === 1) {
    const runId = [...referencedKeys.values()][0]
    const result = scope.bind(runId)
    if (!result.ok) return result.reason
    bound = runId
  }
  if (bound) {
    const others = [...referencedKeys.entries()]
      .filter(([key]) => key !== runKey(bound))
      .map(([, id]) => id)
      .sort()
    if (others.length > 0) {
      return `当前任务只允许访问 runs/${bound}，命令引用了其他任务: ${others}`
    }
  }

  for (const target of extractedPaths(command, REDIRECTION)) {
    if (isNullSink(target)) continue
    const path = resolvedPath(target, root)
    if (!isInside(target, root)) {
      return `禁止向项目目录外重定向写入: ${target}`
    }
    if (isProtectedWrite(path, root)) {
      return `受保护路径只允许读取/执行，禁止重定向写入: ${target}`
    }
    const candidate = runIdForPath(path, root)
    if (bound && (candidate === null || runKey(candidate) !== runKey(bound))) {
      return `任务执行期间只允许写入 runs/${bound}: ${target}`
    }
  }

  const quoted = quotedSpans(command)
  const checkWriteKeyword = (keyword, segment) => {
    const normalized = segment.replace(/\\/g, "/").toLowerCase()
    if (PROTECTED_SHELL_REFERENCE.test(normalized)) {
      return `受保护路径只允许读取/执行，禁止 ${keyword}`
    }
    if (bound && !normalized.includes(`runs/${bound.toLowerCase()}`)) {
      return `任务执行期间 ${keyword} 的写入目标必须明确位于 runs/${bound}`
    }
    if (/[$%][A-Za-z_{]/.test(segment)) {
      return `禁止使用未解析变量执行写入/删除命令 ${keyword}；请改用明确路径`
    }
    for (const target of extractedPaths(segment, EXTERNAL_PATH)) {
      const path = resolvedPath(target, root)
      if (!isInside(target, root)) {
        return `禁止 ${keyword} 操作项目目录外路径: ${target}`
      }
      if (isProtectedWrite(path, root)) {
        return `受保护路径只允许读取/执行，禁止 ${keyword}: ${target}`
      }
      const candidate = runIdForPath(path, root)
      if (bound && (candidate === null || runKey(candidate) !== runKey(bound))) {
        return `任务执行期间只允许写入 runs/${bound}: ${target}`
      }
    }
    return null
  }
  // 显式写/删命令与 shell 改写命令（sed -i / git apply / tar 解压等）共用同一套目标检查。
  // 检查段从命中的命令名开始取到段尾：find <目录> -delete、git apply --directory=
  // 这类「目标在选项前/中」的形式也要覆盖，不能只看关键词之后的文本。
  for (const [pattern, fallbackKeyword] of [
    [WRITE_OR_DELETE, null],
    [SHELL_FILE_MUTATORS, "shell 改写命令"],
  ]) {
    for (const match of command.matchAll(globalize(pattern))) {
      if (quoted.some(([start, end]) => match.index >= start && match.index < end)) continue
      const segment = command
        .slice(match.index)
        .split(/&&|\|\||[;&|\n]/)[0]
      const verdict = checkWriteKeyword(fallbackKeyword || match[1], segment)
      if (verdict) return verdict
    }
  }
  return null
}

async function guard(tool, args, sessionID, root, client) {
  const scope = new Scope(root, await resolveScopeKey(client, sessionID))
  if (FILE_READ_TOOLS.has(tool) || FILE_WRITE_TOOLS.has(tool)) {
    const denial = guardFile(tool, args, root, scope)
    return denial ? { decision: "deny", reason: denial } : null
  }
  if (tool === "bash") {
    const command = String((args && args.command) || "")
    const denial = guardShell(command, root, scope)
    if (denial) return { decision: "deny", reason: denial }
    // 询问类判断不在 tool.execute.before 处理：throw 实现的「ask」没有批准通道，
    // 等于永久拒绝（用户点了允许也执行不了）。高风险命令的询问由 opencode.json
    // 静态 ask 规则承担（原生确认框，批准后可执行）；插件在 permission.ask
    // 钩子里对进入询问流程的命令做硬规则复查（见 GuardPlugin）。
    return null
  }
  return null
}

export const GuardPlugin = async ({ directory, worktree, client }) => {
  const projectDir = directory || worktree || process.cwd()
  return {
    "tool.execute.before": async (input, output) => {
      let result = null
      try {
        result = await guard(input.tool, output && output.args, input.sessionID, projectDir, client)
      } catch (error) {
        // fail-closed：守卫自身异常 → 默认拦截（宁误拦不漏放），报错文案保留该术语。
        const detail = error instanceof Error ? error.message : String(error)
        throw new Error(`守卫插件自检失败（fail-closed），需要用户确认: ${detail}`)
      }
      if (!result) return
      if (result.decision === "deny") {
        throw new Error(result.reason || "操作被项目权限策略拒绝")
      }
    },
    // 询问流程复查：opencode 静态 ask 规则弹出原生确认框前后触发本钩子。
    // 插件用与 tool.execute.before 同一套硬规则复查命令——命中硬规则的改判
    // deny（不给批准通道）；其余保持 ask，用户在原生确认框批准即可执行。
    "permission.ask": async (input, output) => {
      const meta = (input && input.metadata) || {}
      const metaArgs = meta.args && typeof meta.args === "object" ? meta.args : {}
      const command =
        typeof meta.command === "string"
          ? meta.command
          : typeof metaArgs.command === "string"
            ? metaArgs.command
            : null
      if (!command) return
      let denial = null
      try {
        const scope = new Scope(projectDir, await resolveScopeKey(client, input.sessionID))
        denial = guardShell(command, projectDir, scope)
      } catch {
        return // 复查自身故障不干预，交由原生询问流程兜底（弹窗即天然拦截）
      }
      if (denial) output.status = "deny"
    },
  }
}

export default GuardPlugin
