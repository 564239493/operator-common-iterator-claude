import { existsSync } from "node:fs"
import { join } from "node:path"

const TOOL_MAP = {
  read: "Read",
  glob: "Glob",
  grep: "Grep",
  edit: "Edit",
  write: "Write",
  apply_patch: "Edit",
  bash: "Bash",
  task: "Agent",
}

function pythonExe(projectDir) {
  const candidates = [
    join(projectDir, ".venv", "Scripts", "python.exe"),
    join(projectDir, ".venv", "bin", "python"),
    join(projectDir, ".venv", "Scripts", "python"),
  ]
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }
  return null
}

function buildPayload(tool, args, sessionID, cwd) {
  const mapped = TOOL_MAP[tool]
  if (!mapped) return null
  const a = args || {}
  let tool_input
  if (mapped === "Read" || mapped === "Edit" || mapped === "Write") {
    tool_input = { file_path: a.filePath }
  } else if (mapped === "Glob" || mapped === "Grep") {
    tool_input = { path: a.path }
  } else if (mapped === "Bash") {
    tool_input = { command: a.command }
  } else {
    tool_input = { isolation: "" }
  }
  return {
    hook_event_name: "PreToolUse",
    session_id: sessionID,
    tool_name: mapped,
    tool_input,
    cwd,
  }
}

function withTimeout(promise, ms, onTimeout) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try {
        onTimeout()
      } catch {}
      reject(new Error("hook timeout"))
    }, ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      }
    )
  })
}

async function runGuard(payload, projectDir, client) {
  const exe = pythonExe(projectDir)
  const script = join(projectDir, ".claude", "hooks", "guard_project_writes.py")
  if (!exe || !existsSync(script)) {
    try {
      await client.app.log({
        body: {
          service: "guard-project-writes",
          level: "error",
          message: `guard_project_writes.py 无法运行: python=${exe}, script=${script}`,
        },
      })
    } catch {}
    return null
  }
  const proc = Bun.spawn([exe, "-X", "utf8", script], {
    cwd: projectDir,
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
    env: {
      ...process.env,
      CLAUDE_PROJECT_DIR: projectDir,
      OPENCODE_COMPAT: "true",
    },
  })
  try {
    proc.stdin.write(JSON.stringify(payload))
    proc.stdin.end()
  } catch {}
  let code = -1
  try {
    code = await withTimeout(proc.exited, 10000, () => {
      try {
        proc.kill()
      } catch {}
    })
  } catch {
    return null
  }
  let stdout = ""
  try {
    stdout = await new Response(proc.stdout).text()
  } catch {}
  if (code !== 0) return null
  try {
    const parsed = JSON.parse(stdout.trim())
    const output = parsed && parsed.hookSpecificOutput
    if (output && output.permissionDecision) {
      return { decision: output.permissionDecision, reason: output.permissionDecisionReason }
    }
  } catch {}
  return null
}

export const GuardProjectWritesPlugin = async ({ directory, worktree, client }) => {
  const projectDir = directory || worktree || process.cwd()
  return {
    "tool.execute.before": async (input, output) => {
      const payload = buildPayload(input.tool, output.args, input.sessionID, projectDir)
      if (!payload) return
      const result = await runGuard(payload, projectDir, client)
      if (!result) return
      if (result.decision === "deny") {
        throw new Error(result.reason || "操作被项目权限策略拒绝")
      }
      if (result.decision === "ask") {
        throw new Error("需要用户确认: " + (result.reason || ""))
      }
    },
  }
}

export default GuardProjectWritesPlugin
