// guard.js 规则回归测试：bun test ./.opencode/plugins/guard.test.js
// （bun test 对隐藏目录不做自动发现，路径必须带 ./ 前缀；仅支持 bun 运行时。）
// 只测纯函数与 guard() 决策，不启动 opencode、不做真实文件系统 scope 读写
// （scope 文件写入 tmp 目录隔离）。

import { describe, expect, test, beforeEach, afterAll } from "bun:test"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import os from "node:os"

const guardMod = await import("./guard.js")

const TMP = join(os.tmpdir(), "guard-test-root")
const RUN = "aclnnTest-20260928-000000-000000"

beforeEach(() => {
  rmSync(TMP, { recursive: true, force: true })
  mkdirSync(join(TMP, "runs", RUN), { recursive: true })
  mkdirSync(join(TMP, "executer"), { recursive: true })
  mkdirSync(join(TMP, ".opencode", "runtime", "task_scopes"), { recursive: true })
})

afterAll(() => {
  rmSync(TMP, { recursive: true, force: true })
})

async function hookGuard(tool, args, session = "s-test") {
  const hooks = await guardMod.default({ directory: TMP })
  let captured = null
  try {
    await hooks["tool.execute.before"]({ tool, sessionID: session, callID: "c1" }, { args })
  } catch (error) {
    captured = error.message
  }
  return captured
}

describe("受保护路径", () => {
  test("edit executer 拒绝", async () => {
    const msg = await hookGuard("edit", { filePath: "executer/runner.py", oldString: "a", newString: "b" })
    expect(msg).toContain("受保护路径")
  })
  test("write servers.json 拒绝", async () => {
    const msg = await hookGuard("write", { filePath: "servers.json", content: "x" })
    expect(msg).toContain("受保护路径")
  })
  test("apply_patch 补丁文本逐文件送检（真实 patchText 格式）", async () => {
    const msg = await hookGuard("apply_patch", {
      patchText: "*** Begin Patch\n*** Update File: executer/ssh.py\n@@\n-a\n+b\n*** End Patch",
    })
    expect(msg).toContain("受保护路径")
  })
  test("apply_patch Move to 目标送检", async () => {
    const msg = await hookGuard("apply_patch", {
      patchText: "*** Begin Patch\n*** Update File: notes.txt\n*** Move to: executer/runner.py\n@@\n-a\n+b\n*** End Patch",
    })
    expect(msg).toContain("受保护路径")
  })
  test("apply_patch 跨 run 写拒绝（绑定后）", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-ap1")
    const msg = await hookGuard(
      "apply_patch",
      { patchText: "*** Begin Patch\n*** Add File: runs/otherRun-1/evil.txt\n+x\n*** End Patch" },
      "s-ap1",
    )
    expect(msg).toContain("只允许访问")
  })
  test("apply_patch 写当前 run 放行（绑定后）", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-ap2")
    const msg = await hookGuard(
      "apply_patch",
      { patchText: `*** Begin Patch\n*** Add File: runs/${RUN}/iter_001/x.txt\n+v\n*** End Patch` },
      "s-ap2",
    )
    expect(msg).toBeNull()
  })
  test("普通项目文件读放行", async () => {
    const msg = await hookGuard("read", { filePath: "README.md" })
    expect(msg).toBeNull()
  })
})

describe("run 隔离", () => {
  test("读其他 run 拒绝", async () => {
    mkdirSync(join(TMP, "runs", "otherRun-1"), { recursive: true })
    const first = await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-iso")
    expect(first).toBeNull()
    const second = await hookGuard("read", { filePath: "runs/otherRun-1/run_state.json" }, "s-iso")
    expect(second).toContain("只允许访问")
  })
  test("绑定后写项目根（run 外）拒绝", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-iso2")
    const msg = await hookGuard("write", { filePath: "notes.md", content: "x" }, "s-iso2")
    expect(msg).toContain("只允许写入")
  })
  test("绑定后写当前 run 放行", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-iso3")
    const msg = await hookGuard("write", { filePath: `runs/${RUN}/iter_001/cases.json`, content: "{}" }, "s-iso3")
    expect(msg).toBeNull()
  })
  test("bash 引用两个 run 拒绝（未绑定）", async () => {
    const msg = await hookGuard("bash", { command: `ls runs/${RUN} runs/otherRun-1` }, "s-multi")
    expect(msg).toContain("多个任务")
  })
  test("bash 跨 run 遍历拒绝", async () => {
    const msg = await hookGuard("bash", { command: `cat runs/${RUN}/../otherRun-1/x.json` }, "s-trav")
    expect(msg).toContain("跨任务")
  })
})

describe("内联 python 与包装监控", () => {
  test("python -c 拒绝", async () => {
    const msg = await hookGuard("bash", { command: `python -c "print(1)"` }, "s-pyc")
    expect(msg).toContain("python -c")
  })
  test("heredoc stdin python 拒绝", async () => {
    const msg = await hookGuard("bash", { command: "python - <<'EOF'\nprint(1)\nEOF" }, "s-pyh")
    expect(msg).toContain("内联代码")
  })
  test("generation_progress 包装监控拒绝", async () => {
    const msg = await hookGuard("bash", {
      command: `while true; do python scripts/generation_progress.py status --output-dir runs/${RUN}/iter_001; sleep 60; done`,
    }, "s-gen")
    expect(msg).toContain("generation_progress")
  })
  test("python -X utf8 -c 拒绝（编码开关不得绕过）", async () => {
    const msg = await hookGuard("bash", { command: 'python -X utf8 -c "print(1)"' }, "s-pyx")
    expect(msg).toContain("python -c")
  })
  test("py -c 拒绝（Windows 启动器变体）", async () => {
    const msg = await hookGuard("bash", { command: 'py -c "print(1)"' }, "s-pyc2")
    expect(msg).toContain("python -c")
  })
  test("pythonw -c 拒绝（无窗口解释器变体）", async () => {
    const msg = await hookGuard("bash", { command: 'pythonw -c "print(1)"' }, "s-pyw")
    expect(msg).toContain("python -c")
  })
  test("node -e 拒绝（内联代码）", async () => {
    const msg = await hookGuard("bash", { command: 'node -e "require(\'fs\').writeFileSync(\'x\', \'y\')"' }, "s-node")
    expect(msg).toContain("内联代码")
  })
})

describe("只读命令引号内关键词不误伤（deny 层）", () => {
  test("grep 引号内 mkdir 不 deny", () => {
    const msg = guardMod.guardShell('grep -n "mkdir" scripts/init_run.py', TMP, new guardMod.Scope(TMP, "s-q1"))
    expect(msg).toBeNull()
  })
  test("grep 引号内 rm 不 deny", () => {
    const msg = guardMod.guardShell('grep -rn "rm " scripts/', TMP, new guardMod.Scope(TMP, "s-q2"))
    expect(msg).toBeNull()
  })
  test("git log --format 不 deny", () => {
    const msg = guardMod.guardShell('git log --format="%h %s"', TMP, new guardMod.Scope(TMP, "s-q3"))
    expect(msg).toBeNull()
  })
  test("绑定后 grep 引号内 mkdir 全链路放行", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-q4")
    const msg = await hookGuard("bash", { command: 'grep -n "mkdir" scripts/init_run.py' }, "s-q4")
    expect(msg).toBeNull()
  })
})

describe("scope run_id 格式校验", () => {
  test("残留 run_id '*' 视为未绑定，可重新绑定真实 run", async () => {
    writeFileSync(join(TMP, ".opencode", "runtime", "task_scopes", "s-star.json"), '{"run_id": "*"}\n')
    const msg = await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-star")
    expect(msg).toBeNull()
  })
  test("残留 run_id 空串视为未绑定", async () => {
    writeFileSync(join(TMP, ".opencode", "runtime", "task_scopes", "s-empty.json"), '{"run_id": ""}\n')
    const msg = await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-empty")
    expect(msg).toBeNull()
  })
})

describe("高风险与 ask 语义（询问归静态规则，插件只硬拦）", () => {
  test("pip install 插件不再拦截（静态 ask 弹原生确认，批准后可执行）", async () => {
    const msg = await hookGuard("bash", { command: "pip install requests" }, "s-pip")
    expect(msg).toBeNull()
  })
  test("项目内 .py 入口放行", async () => {
    const msg = await hookGuard("bash", { command: "python scripts/generate_cases.py --help" }, "s-pyok")
    expect(msg).toBeNull()
  })
  test("项目外 python 文件插件不再拦（静态 'python /*' ask）", async () => {
    const msg = await hookGuard("bash", { command: "python /tmp/evil.py" }, "s-pyext")
    expect(msg).toBeNull()
  })
  test("未绑定时 rm 插件不再拦（静态 'rm *' ask）", async () => {
    const msg = await hookGuard("bash", { command: "rm somefile.tmp" }, "s-rm")
    expect(msg).toBeNull()
  })
})

describe("shell 层改文件命令（启发式补位，替代已删除的 OS sandbox）", () => {
  test("sed -i 改受保护路径拒绝", () => {
    const msg = guardMod.guardShell("sed -i 's/a/b/' executer/runner.py", TMP, new guardMod.Scope(TMP, "s-sed1"))
    expect(msg).toContain("受保护路径")
  })
  test("git apply 指向 executer 拒绝", () => {
    const msg = guardMod.guardShell("git apply patch.diff --directory=executer", TMP, new guardMod.Scope(TMP, "s-ga1"))
    expect(msg).toContain("受保护路径")
  })
  test("tar 解压到 executer 拒绝", () => {
    const msg = guardMod.guardShell("tar -xf bundle.tar -C executer", TMP, new guardMod.Scope(TMP, "s-tar1"))
    expect(msg).toContain("受保护路径")
  })
  test("find 在 executer 下 -delete 拒绝（目标在选项前）", () => {
    const msg = guardMod.guardShell("find executer -name '*.pyc' -delete", TMP, new guardMod.Scope(TMP, "s-fd1"))
    expect(msg).toContain("受保护路径")
  })
  test("绑定 run 后 sed -i 目标不在当前 run 拒绝", async () => {
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-sed2")
    const msg = await hookGuard("bash", { command: "sed -i 's/a/b/' notes.md" }, "s-sed2")
    expect(msg).toContain("写入目标必须明确位于")
  })
  test("未绑定时 sed -i 普通项目文件放行（与 edit 工具同语义）", () => {
    const msg = guardMod.guardShell("sed -i 's/a/b/' notes.md", TMP, new guardMod.Scope(TMP, "s-sed3"))
    expect(msg).toBeNull()
  })
  test("unzip -d 到 .git 拒绝", () => {
    const msg = guardMod.guardShell("unzip -o bundle.zip -d .git", TMP, new guardMod.Scope(TMP, "s-uz1"))
    expect(msg).toContain("受保护路径")
  })
})

describe("环境变量文件读取（shell 层）", () => {
  test("cat .env 拒绝", () => {
    const msg = guardMod.guardShell("cat .env", TMP, new guardMod.Scope(TMP, "s-env1"))
    expect(msg).toContain("环境变量文件")
  })
  test("cat .env.production 拒绝", () => {
    const msg = guardMod.guardShell("cat .env.production", TMP, new guardMod.Scope(TMP, "s-env2"))
    expect(msg).toContain("环境变量文件")
  })
  test("cat .env.example 放行", () => {
    const msg = guardMod.guardShell("cat .env.example", TMP, new guardMod.Scope(TMP, "s-env3"))
    expect(msg).toBeNull()
  })
  test("引号内 .env 字面量不误伤", () => {
    const msg = guardMod.guardShell('grep -n ".env" scripts/init_run.py', TMP, new guardMod.Scope(TMP, "s-env4"))
    expect(msg).toBeNull()
  })
})

describe("permission.ask 钩子（询问流程复查）", () => {
  async function hookAsk(metadata, session = "s-ask") {
    const hooks = await guardMod.default({ directory: TMP })
    const output = { status: "ask" }
    await hooks["permission.ask"](
      { sessionID: session, metadata, title: "bash" },
      output,
    )
    return output.status
  }
  test("命中硬规则的命令改判 deny（跨 run 访问）", async () => {
    mkdirSync(join(TMP, "runs", "otherRun-2"), { recursive: true })
    await hookGuard("read", { filePath: `runs/${RUN}/run_state.json` }, "s-ask1")
    const status = await hookAsk({ command: `cat runs/otherRun-2/run_state.json` }, "s-ask1")
    expect(status).toBe("deny")
  })
  test("干净命令保持 ask（用户批准后可执行）", async () => {
    const status = await hookAsk({ command: "pip install requests" }, "s-ask2")
    expect(status).toBe("ask")
  })
  test("metadata 无命令时不干预", async () => {
    const status = await hookAsk({}, "s-ask3")
    expect(status).toBe("ask")
  })
})

describe("子代理共享主会话的 run 绑定", () => {
  test("子代理会话沿用根会话绑定（跨 run 访问拒绝）", async () => {
    mkdirSync(join(TMP, "runs", "otherRun-9"), { recursive: true })
    const fakeClient = {
      session: {
        get: async ({ path }) => (path.id === "ses_childA" ? { parentID: "ses_mainA" } : {}),
      },
    }
    const hooks = await guardMod.default({ directory: TMP, client: fakeClient })
    let captured = null
    try {
      await hooks["tool.execute.before"](
        { tool: "read", sessionID: "ses_mainA", callID: "c1" },
        { args: { filePath: `runs/${RUN}/run_state.json` } },
      )
    } catch (error) { captured = error.message }
    expect(captured).toBeNull()
    try {
      await hooks["tool.execute.before"](
        { tool: "read", sessionID: "ses_childA", callID: "c2" },
        { args: { filePath: "runs/otherRun-9/run_state.json" } },
      )
    } catch (error) { captured = error.message }
    expect(captured).toContain("只允许访问")
  })

  test("父级链查询失败时退化为按会话各自记账", async () => {
    mkdirSync(join(TMP, "runs", "otherRun-10"), { recursive: true })
    const brokenClient = {
      session: {
        get: async () => { throw new Error("unreachable") },
      },
    }
    const hooks = await guardMod.default({ directory: TMP, client: brokenClient })
    let captured = null
    try {
      await hooks["tool.execute.before"](
        { tool: "read", sessionID: "ses_mainB", callID: "c1" },
        { args: { filePath: `runs/${RUN}/run_state.json` } },
      )
    } catch (error) { captured = error.message }
    expect(captured).toBeNull()
    try {
      await hooks["tool.execute.before"](
        { tool: "read", sessionID: "ses_childB", callID: "c2" },
        { args: { filePath: "runs/otherRun-10/run_state.json" } },
      )
    } catch (error) { captured = error.message }
    expect(captured).toBeNull()
  })
})

describe("fail-closed", () => {
  test("守卫内部异常转为 fail-closed 报错，不静默放行", async () => {
    // 构造一个「访问任何属性都抛异常」的 args，逼 guard() 在内部崩溃
    const exploding = new Proxy({}, { get() { throw new Error("boom") } })
    const msg = await hookGuard("bash", exploding, "s-fail")
    expect(msg).toContain("fail-closed")
    expect(msg).toContain("boom")
  })
})
