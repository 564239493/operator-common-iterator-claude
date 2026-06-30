---
description: 以 mock 或 real 模式执行 cases.json，并输出标准 execution_result.json。
---

# 用例执行规范

默认命令：

```text
python scripts/execute_cases.py --mode real --cases <cases.json> --output <execution_result.json> --doc <run内文档快照> --operator <算子名> --server-config <servers.json> --run-id <run-id>
```

真实执行是默认行为。配置缺失时停止并提示用户补充，禁止回退 Mock。只有用户明确传入
`--mode mock` 时，才运行 Mock 用例：

```text
python scripts/execute_cases.py --mode mock --cases <cases.json> --output <execution_result.json>
```

执行结束后运行 `python scripts/validate_artifacts.py execution <execution_result.json>`。
网络、认证、环境和框架故障写入 `engine_error`，不要伪装成普通 case fail。
