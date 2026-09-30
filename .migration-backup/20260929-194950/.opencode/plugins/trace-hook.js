import { existsSync } from "node:fs"
import { join } from "node:path"

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

async function runTrace(payload, projectDir) {
  const exe = pythonExe(projectDir)
  const script = join(projectDir, ".claude", "hooks", "trace_hook.py")
  if (!exe || !existsSync(script)) return
  try {
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
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 5000)
      proc.exited.then(
        () => {
          clearTimeout(timer)
          resolve()
        },
        () => {
          clearTimeout(timer)
          resolve()
        }
      )
    })
  } catch {}
}

export const TraceHookPlugin = async ({ directory, worktree }) => {
  const projectDir = directory || worktree || process.cwd()
  return {
    event: async ({ event }) => {
      if (event.type === "session.created") {
        const id = event.properties && event.properties.id
        await runTrace(
          { hook_event_name: "SessionStart", session_id: id, cwd: projectDir },
          projectDir
        )
      }
    },
    "tool.execute.before": async (input, output) => {
      if (input.tool === "task") {
        await runTrace(
          {
            hook_event_name: "SubagentStart",
            session_id: input.sessionID,
            agent_type: (output.args && output.args.subagent_type) || "unknown",
            agent_id: input.callID,
            cwd: projectDir,
          },
          projectDir
        )
      }
    },
    "tool.execute.after": async (input) => {
      if (input.tool === "task") {
        await runTrace(
          {
            hook_event_name: "SubagentStop",
            session_id: input.sessionID,
            agent_type: (input.args && input.args.subagent_type) || "unknown",
            agent_id: input.callID,
            cwd: projectDir,
          },
          projectDir
        )
      }
    },
  }
}

export default TraceHookPlugin
