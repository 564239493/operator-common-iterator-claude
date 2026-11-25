# PICT 预编译二进制

本目录存放项目自带的 PICT 可执行文件，供无 PICT 环境的机器直接使用，**用户无需安装任何东西**。

## 目录约定

```
third_party/pict/bin/<平台>-<架构>/pict[.exe]
```

| 路径 | 平台 | 状态 |
|---|---|---|
| `bin/linux-aarch64/pict` | Linux aarch64（ARM64） | ✅ 全静态 |
| `bin/linux-x86_64/pict` | Linux x86_64 | ✅ 全静态 |
| `bin/win-x64/pict.exe` | Windows x64 | ✅ 官方发布件 |

> 注：Windows 用户目前把 `pict.exe` 放在**项目根目录**即可被 `shutil.which()` 找到
> （Windows 上 `shutil.which` 会把当前目录前置到 PATH），Linux 没有这个行为，
> 所以 Linux 二进制需要由代码显式按上表路径查找。

所有二进制均来自 microsoft/pict **v3.7.4**（MIT），源码 commit
`d529bb1535b725c8f29704f183b23dd344895d02`。

---

## linux-aarch64

| 项 | 值 |
|---|---|
| 产物 | `bin/linux-aarch64/pict` |
| 产物 sha256 | `f476106e3fa4808d68300cfd4e39ea10bce9166407dd4814ff69277f7e825e2a` |
| 产物大小 | 2,562,448 字节（约 2.44 MiB） |
| 链接方式 | **全静态**，`ldd` → `not a dynamic executable` |
| 构建主机 | aarch64 / Ubuntu 22.04.2 LTS，glibc 2.35，g++ 11.4.0，GNU Make 4.3 |

```bash
tar xzf pict-3.7.4-src.tar.gz
cd pict-3.7.4
make -j"$(nproc)" CXX='g++ -static'
```

## linux-x86_64

| 项 | 值 |
|---|---|
| 产物 | `bin/linux-x86_64/pict` |
| 产物 sha256 | `91da52077f4ec077f2ed1fc8c7614eb56d30863431248051dcfa8d3ede0fc15d` |
| 产物大小 | 2,843,800 字节（约 2.71 MiB） |
| 链接方式 | **全静态**，`ldd` → `not a dynamic executable` |
| 构建方式 | 在 **aarch64** 主机上交叉编译（非原生 x86 机器） |
| 交叉工具链 | `g++-x86-64-linux-gnu` 11.4.0 + `libc6-dev-amd64-cross` |

```bash
apt install g++-x86-64-linux-gnu libc6-dev-amd64-cross

# 见下方"交叉编译的 libm 坑"，这里已用 syslib 垫片绕开
mkdir -p syslib
printf 'GROUP ( /usr/x86_64-linux-gnu/lib/libm-2.35.a /usr/x86_64-linux-gnu/lib/libmvec.a )\n' > syslib/libm.a

tar xzf pict-3.7.4-src.tar.gz -C x86
cd x86/pict-3.7.4
make -j"$(nproc)" CXX='x86_64-linux-gnu-g++ -static -L'"$PWD/../../syslib"
```

### 交叉编译的 libm 坑

`-lm` 是 g++ 驱动自动追加的，`ld` 会解析 `/usr/x86_64-linux-gnu/lib/libm.a`
—— 而 Ubuntu 交叉包提供的这个文件是个 **ld 脚本**，内容里硬编码了 **amd64 原生机路径**：

```
GROUP ( /usr/lib/x86_64-linux-gnu/libm-2.35.a /usr/lib/x86_64-linux-gnu/libmvec.a )
```

在 arm64 宿主上 `/usr/lib/x86_64-linux-gnu/` 不存在，于是链接报
`cannot find /usr/lib/x86_64-linux-gnu/libm-2.35.a`。真正的静态库其实就在
`/usr/x86_64-linux-gnu/lib/` 下。

**解法**：在构建目录放一个内容正确、名字相同的 `libm.a` 脚本，并用 `-L` 让它先命中，
**不改动任何系统文件**。若 `-L` 顺序不生效，用 `LIBRARY_PATH=<垫片目录>` 也能达到同样效果。

## win-x64

| 项 | 值 |
|---|---|
| 产物 | `bin/win-x64/pict.exe` |
| 产物 sha256 | `80aba862739cf18b4faa13d408163324d188a1c4efccdd977d9c5ba3f8950bbd` |
| 产物大小 | 207,360 字节（约 202 KiB） |
| 来源 | 官方 Release 资产（asset id `61100880`），非自建 |

```bash
curl -sL -H "Accept: application/octet-stream" \
  -o pict.exe https://api.github.com/repos/microsoft/pict/releases/assets/61100880
```

> 本机 `github.com` 不可达，但 `api.github.com` 可达，故走 API 资产端点
> （会 302 到 `objects.githubusercontent.com`）。直接访问
> `github.com/.../releases/download/...` 会超时。

---

## 校验方式

Linux（`file` 不一定装，用 `od` 直读 ELF 头）：

```bash
od -An -tx1 -N20 pict   # 偏移 18: aarch64 = b7 00, x86_64 = 3e 00
ldd pict                # 期望: not a dynamic executable
sha256sum pict
./pict smoke_model.txt  # 期望: 输出参数组合表
```

Windows：

```powershell
.\pict.exe smoke_model.txt
```

三个平台的冒烟结果均为 3 参数模型 → **9 行组合表，A×B 全组合覆盖，内容一致**。
x86_64 产物在 WSL Ubuntu 22.04（真 x86_64）上验证通过；ARM 产物在构建机原地验证。

## 为什么 Linux 选全静态

| 方式 | 大小 | 依赖要求 | 可移植性 |
|---|---|---|---|
| 默认动态链接 | 384 KB | GLIBC_2.34+ / GLIBCXX_3.4.30+ | 差，老系统直接 `GLIBC_2.34 not found` |
| `-static-libstdc++ -static-libgcc` | 1.81 MB | 仍需 **GLIBC_2.34+** | 一般 |
| **`-static`** | 2.44 / 2.71 MiB | **无** | 最好，任意同架构 Linux 可直接跑 |

中间档看着"静态"，实际只静态了 libstdc++/libgcc，**glibc 下限一点没降**，是最容易踩的坑。

## Git 注意事项

Windows 工作副本上新增的二进制**没有可执行位**。提交时必须显式设置，否则 Linux 上会
`Permission denied`：

```bash
git update-index --chmod=+x third_party/pict/bin/linux-aarch64/pict
git update-index --chmod=+x third_party/pict/bin/linux-x86_64/pict
git ls-files -s third_party/pict/bin/   # 期望前缀 100755
```

（`win-x64/pict.exe` 保持 `100644` 即可。）

## 代码接入与回退

解析入口在 `agent/generators/operator_param_combine/combination_result_generator/generator/pict_generator.py`：

1. `_project_root()` 从本文件向上找含 `third_party/pict` 的目录；
2. `_bundled_pict_dir()` 按 `sys.platform` + `platform.machine()` 映射到
   `bin/{win-x64|win-arm64|linux-x86_64|linux-aarch64}/`，缺可执行位时尽力 `chmod +x`；
3. `resolve_pict_command()` 的优先级：显式 `pict_exe` > `PICT_EXE` > `PATH` >
   **随项目自带** > WSL > 全盘搜索。

自带二进制**不是无条件信任**：`resolve_pict_command_with_fallback()` 会先跑一次
`p1: 1, 2 / p2: x, y` 的探针（结果按可执行文件路径缓存在 `_BUNDLED_PROBE_CACHE`，
一次进程只探一次，约 30 ms）。探针失败（架构不符、缺库、无执行位、被安全软件拦截）就
**当作不存在**，用 `include_bundled=False` 重走原有解析链，行为与接入前完全一致，
只多打一条 warning 日志：

```
bundled pict unavailable, falling back to original resolution: bundled=... fallback=...
```

`check_pict_environment()` 与 `execute_pict()` 都走这个带兜底的入口。
