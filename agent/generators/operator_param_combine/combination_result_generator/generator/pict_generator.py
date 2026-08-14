"""
PICT 真实工具集成 + PICT-compatible 生成器。

本模块是 PICT 的确定性工具集，仅使用 Python 标准库，Windows / Linux(WSL) 均可运行。

内容分层:
1. PICTGenerator          —— BaseGenerator 子类：实现 initialize/build/finalize，
                               优先调用真实 PICT，不可用/失败时回退 CoverageDrivenGenerator。
                               回退时通过日志详细记录不可执行的原因（环境诊断 / 失败分类）。
2. 命令与环境探测           —— resolve_pict_command / check_pict_environment：
                              确定 PICT 执行命令（Linux: `pict model /o:N`；Windows: `pict.exe model /o:N`，
                              并支持 Windows 下经 WSL 执行 `wsl.exe -d <distro> -- pict ...`），
                              探测是否可执行，Windows 缺 32 位 VC++ 运行时(MSVCP140/VCRUNTIME140)时给出诊断。
3. PICT 输入转换工具        —— convert_domain_json_to_pict_model：
                             把 domain_data dict（parameters + constraints，无 cases）直接转换为
                              PICT 可接受的 model txt，保证输出内容绝对合法：
                             空值域列 / 非标量取值被剔除；约束经 AST 尽力翻译为 PICT 语法，
                             无法翻译的约束原地剔除并记录 warning。
4. PICT 执行方法           —— execute_pict：
                              失败分类（pict_not_found / launch_failure / timeout /
                              constraint_parse_error / model_syntax_error / no_output / over_constrained），
                              每轮日志留痕；遇到非法约束导致执行失败时，多轮剔除被 PICT 点名的约束直到执行成功；
                              结果以 {base}_raw.tsv + {base}_cases.json + {base}_report.json 保存。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import \
    ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.generator import BaseGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import \
    CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_driven_generator import \
    CoverageDrivenGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import \
    GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult, \
    TestSuite, TestCase, TestValue
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.common_utils.timing import track

logger = LazyLogger()


class PICTGenerator(BaseGenerator):
    """PICT-compatible 生成器（基于 BaseGenerator 生命周期的真实 PICT 集成）。

    build() 优先调用真实 PICT（外部工具，见 execute_pict）生成 pairwise 用例；
    当真实 PICT 不可用（功能点1：环境探测失败，区分 Windows/Linux/WSL）、
    或执行失败（功能点4：有限次修复仍失败）时，
    自动回退到进程内 CoverageDrivenGenerator（功能点2），保证结果确定可用，
    并在日志中详细记录回退原因。

    真实 PICT 的输入来自 domain_data dict（功能点3，见 convert_domain_json_to_pict_model），
    若未提供则退回从 coverage 宇宙重建模型（兼容旧调用方）。
    operator_name 仅用于日志留痕，可选。

    生命周期：generate() -> initialize() -> build() -> finalize()。
    真实 PICT 路径的执行报告保存在 `pict_report`（PictRunReport）。
    """

    def __init__(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
        constraint: Optional[ConstraintProtocol],
        config: GeneratorOptions,
        candidate_generator: CandidateGenerator,
        pair_builder: PairBuilder,
        *,
        operator_name: str = "",
        candidate_pool_size: int = 100,
        pict_exe: Optional[str] = None,
        wsl_distro: Optional[str] = None,
        pict_timeout: int = 60,
        pict_max_rounds: int = 20,
        use_real_pict: Optional[bool] = None,
        result_output_path: Optional[str] = None,
        domain_data: Optional[Dict[str, Any]] = None,
        filter_by_constraint: bool = True,
    ) -> None:
        super().__init__(universe, coverage_tracker, constraint, config)

        self._candidate_generator = candidate_generator
        self._pair_builder = pair_builder
        self._candidate_pool_size = candidate_pool_size
        self._pict_exe = pict_exe
        self._wsl_distro = wsl_distro
        self._pict_timeout = pict_timeout
        self._pict_max_rounds = pict_max_rounds
        # None=auto（约束为空且 PICT 可用时启用）；True=强制；False=禁用
        self._use_real_pict = use_real_pict
        self._result_output_path = result_output_path
        self._domain_data = domain_data
        self._operator_name = operator_name
        self._filter_by_constraint = filter_by_constraint
        self._inner = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=coverage_tracker,
            constraint=constraint,
            config=config,
            candidate_generator=candidate_generator,
            pair_builder=pair_builder,
            candidate_pool_size=candidate_pool_size,
        )
        self._pict_report: Optional[PictRunReport] = None
        self._pict_model: Optional[PictModel] = None
        self._pict_col_map: Dict[str, Tuple[str, str]] = {}
        self._pict_used = False

    def initialize(self) -> None:
        """初始化：重置内部回退生成器状态与 PICT 记录。"""
        self._inner.initialize()
        self._pict_report = None
        self._pict_model = None
        self._pict_col_map = {}
        self._pict_used = False

    @track("PICTGenerator.build")
    def build(self) -> GenerationResult:
        """生成用例：优先真实 PICT，失败/不可用时回退进程内生成器。"""
        start_time = time.time()
        use_real = self._use_real_pict
        if use_real is None:
            use_real = True
        if use_real:
            result = self._build_with_real_pict(start_time)
            if result is not None:
                return result
            logger.info("PICTGenerator fell back to in-process generator")
        return self._inner.build()

    def finalize(self) -> None:
        """清理。"""
        self._inner.finalize()

    @track("PICTGenerator._build_with_real_pict")
    def _build_with_real_pict(self, start_time: float) -> Optional[GenerationResult]:
        """用真实 PICT 生成；返回 None 表示不可用/失败，应回退。

        功能点1：先探测环境（区分 Windows 原生 / WSL / Linux）。
        功能点2：不可用或执行失败时，详细记录原因并返回 None 触发回退。
        """
        # 功能点1：环境探测
        env = check_pict_environment(
            pict_exe=self._pict_exe,
            wsl_distro=self._wsl_distro,
            timeout=self._pict_timeout,
        )
        if not env.available:
            logger.warning(
                "operator=%s real PICT unavailable (category=%s, platform=%s); falling back to in-process generator. "
                "diagnostics=%s suggestions=%s",
                self._operator_name, env.failure_category, env.platform, env.diagnostics, env.suggestions,
            )
            return None

        # 功能点3：构建 PICT 模型（domain_data.json -> model.txt）
        model = self._build_pict_model()
        if model is None:
            return None
        self._pict_model = model
        self._pict_col_map = model.col_map

        output_dir = self._result_output_path or os.path.join(tempfile.gettempdir(), "pict_generator_runs")

        # 功能点4：执行（失败分类 + 有限次剔除非法约束）
        report = execute_pict(
            self._operator_name or "",
            model,
            output_dir,
            strength=self._config.strength,
            seed=self._config.random_seed,
            timeout=self._pict_timeout,
            max_rounds=self._pict_max_rounds,
            pict_exe=self._pict_exe,
            wsl_distro=self._wsl_distro,
            write_artifacts=bool(self._result_output_path),
        )
        self._pict_report = report
        if not report.success:
            logger.warning(
                "operator=%s real PICT failed (category=%s); falling back to in-process generator. "
                "rounds=%d removed_constraints=%s",
                self._operator_name, report.failure_category, len(report.rounds), report.removed_constraints,
            )
            return None

        cases, headers = _parse_tsv(report.success_stdout or "")
        if not cases:
            logger.warning("operator=%s real PICT returned no rows, fall back to in-process generator",
                           self._operator_name)
            return None

        self._pict_used = True
        suite = self._pict_rows_to_suite(cases)
        if suite.size() == 0:
            logger.warning("operator=%s PICT rows all filtered by constraint; fall back to in-process generator",
                           self._operator_name)
            return None
        return GenerationResult(
            suite=suite,
            coverage_rate=self._coverage_tracker.coverage_rate(),
            iterations=len(suite.cases),
            elapsed_time=time.time() - start_time,
        )

    @track("PictGenerator._build_pict_model")
    def _build_pict_model(self) -> Optional[PictModel]:
        """构建 PICT 模型。

        优先使用 domain_data dict（功能点3）；未提供时从 coverage 宇宙重建（兼容旧调用方）。
        """
        if self._domain_data:
            model = convert_domain_json_to_pict_model(
                self._operator_name or "",
                self._domain_data,
            )
            if not model.columns or len(model.columns) < 2:
                logger.warning("operator=%s PICT model needs >=2 columns, got %d",
                               self._operator_name, len(model.columns))
                return None
            return model
        return self._build_model_from_universe()

    @track("PictGenerator._build_model_from_universe")
    def _build_model_from_universe(self) -> Optional[PictModel]:
        """从 coverage 宇宙重建 PICT 模型（列名 {param}_{attr}），无约束。

        若可用列少于 2 列（PICT 无法做 pairwise）或宇宙为空则返回 None。
        """
        parameters: Dict[str, List[Any]] = {}
        col_map: Dict[str, Tuple[str, str]] = {}
        for pair in self._universe.get_pairs():
            for fv in (pair.left, pair.right):
                factor = fv.factor
                col = "{}_{}".format(factor.parameter, factor.attribute)
                col_map[col] = (factor.parameter, factor.attribute)
                seen = parameters.setdefault(col, [])
                if _is_scalar(fv.value) and not any(repr(v) == repr(fv.value) for v in seen):
                    seen.append(fv.value)
        cols = [c for c, vals in parameters.items() if vals]
        if len(cols) < 2:
            logger.warning("PICT model needs >=2 columns, got %d", len(cols))
            return None
        parameters = {c: parameters[c] for c in cols}
        col_map = {c: col_map[c] for c in cols}
        model_text = _build_model_text(parameters, [])
        return PictModel(
            model_text=model_text,
            columns=cols,
            parameters=parameters,
            col_map=col_map,
        )

    @track("PictGenerator._pict_rows_to_suite")
    def _pict_rows_to_suite(self, cases: List[Dict[str, Any]]) -> TestSuite:
        """把 PICT 行转成 TestSuite，并同步更新覆盖率。

        携带约束时对每行做黑盒 evaluate 过滤（部分约束无法翻译为 PICT 文本，
        因此 PICT 先生成候选，再由约束二次过滤）。
        """
        suite = TestSuite()
        dropped = 0
        for row in cases:
            test_case = TestCase()
            factor_values = []
            for col, value in row.items():
                mapping = self._pict_col_map.get(col)
                if mapping is None:
                    continue
                param, attr = mapping
                test_case.add_value(TestValue(parameter=param, attribute=attr, value=value))
                factor_values.append(FactorValue(Factor(param, attr), value))
            if not factor_values:
                continue
            if self._filter_by_constraint and self._constraint is not None:
                try:
                    ok = self._constraint.evaluate(test_case.values)
                except Exception as exc:
                    logger.warning("constraint evaluate failed on row: %s", exc)
                    ok = False
                if not ok:
                    dropped += 1
                    continue
            suite.add(test_case)
            pairs = self._pair_builder.build(factor_values)
            self._coverage_tracker.update(pairs)
        if dropped:
            logger.info("PICT rows filtered by constraint: kept=%d dropped=%d", suite.size(), dropped)
        return suite

    @property
    def algorithm_name(self) -> str:
        return "PICT-compatible"

    @property
    def pict_used(self) -> bool:
        """本次 generate() 是否真的走了真实 PICT。"""
        return self._pict_used

    @property
    def pict_report(self) -> Optional[PictRunReport]:
        """最近一次真实 PICT 运行报告（未运行时为 None）。"""
        return self._pict_report


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #

class PictFailureCategory:
    """PICT 执行失败分类（字符串常量，便于 JSON 序列化）。"""

    SUCCESS = "success"
    PICT_NOT_FOUND = "pict_not_found"
    LAUNCH_FAILURE = "launch_failure"
    TIMEOUT = "timeout"
    CONSTRAINT_PARSE_ERROR = "constraint_parse_error"
    MODEL_SYNTAX_ERROR = "model_syntax_error"
    OVER_CONSTRAINED = "over_constrained"
    NO_OUTPUT = "no_output"


@dataclass
class PictCommand:
    """解析得到的 PICT 命令。"""

    cmd_list: List[str]
    executable: str
    platform: str
    via_wsl: bool = False
    wsl_distro: Optional[str] = None


@dataclass
class PictModel:
    """PICT 输入模型。"""

    model_text: str
    columns: List[str]
    parameters: Dict[str, List[Any]]
    col_map: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    constraints: List[str] = field(default_factory=list)
    pict_constraints: List[str] = field(default_factory=list)
    dropped_constraints: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    operator_name: str = ""


@dataclass
class PictEnvironmentReport:
    """环境探测报告。"""

    available: bool
    command: Optional[str]
    platform: str
    failure_category: str
    diagnostics: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    probe_stdout: str = ""
    probe_stderr: str = ""


@dataclass
class PictRoundResult:
    """单轮执行记录。"""

    round_index: int
    constraints_in_use: int
    failure_category: str
    returncode: Optional[int]
    stderr: str
    stdout: str
    removed_constraint: Optional[str] = None


@dataclass
class PictRunReport:
    """一次 execute_pict 的运行报告。"""

    success: bool
    failure_category: str
    rounds: List[PictRoundResult] = field(default_factory=list)
    removed_constraints: List[str] = field(default_factory=list)
    case_count: int = 0
    elapsed_time: float = 0.0
    model_path: str = ""
    raw_output_path: str = ""
    cases_output_path: str = ""
    report_output_path: str = ""
    success_stdout: str = ""


# --------------------------------------------------------------------------- #
# 失败分类 / 命令解析 / 环境探测
# --------------------------------------------------------------------------- #

# Windows 加载器失败退出码（32 位 VC++ 运行时缺失等）
_WIN_LAUNCH_FAILURE_CODES = {
    0xC0000135,  # STATUS_DLL_NOT_FOUND
    0xC0000142,  # STATUS_DLL_INIT_FAILED
    0xC000007B,  # STATUS_INVALID_IMAGE_FORMAT
    0xC0000139,  # STATUS_ENTRYPOINT_NOT_FOUND
}


def _norm_exit_code(rc: Optional[int]) -> Optional[int]:
    """把可能带符号的退出码归一化为无符号 32 位值。"""
    if rc is None:
        return None
    return rc & 0xFFFFFFFF


def _clean_output(text: str) -> str:
    """清理 wsl.exe 横幅等夹杂的 NUL 字符，保留 PICT 自身输出。"""
    return (text or "").replace("\x00", "")


def _looks_like_model_error(combined: str) -> bool:
    hints = (
        "Parameter", "should have at least one value", "Set of values",
        "at least one value defined", "Unknown", "duplicate", "Input Error: Parameter",
        "Incorrect numeric value",
    )
    return any(h.lower() in combined.lower() for h in hints)


def _looks_like_constraint_error(combined: str) -> bool:
    hints = (
        "constraint", "Missing or incorrect relation", "Misplaced THEN",
        "Missing opening bracket", "Incorrect numeric value", "Input Error: IF",
        "unknown parameter", "Input Error:",
    )
    return any(h.lower() in combined.lower() for h in hints)


def _classify_failure(rc: Optional[int], combined: str, cmd: PictCommand) -> str:
    combined = _clean_output(combined)
    if rc == 0:
        return PictFailureCategory.SUCCESS if combined.strip() else PictFailureCategory.NO_OUTPUT
    code = _norm_exit_code(rc)
    if not cmd.via_wsl and cmd.platform == "win32" and code in _WIN_LAUNCH_FAILURE_CODES:
        return PictFailureCategory.LAUNCH_FAILURE
    if "Too restrictive constraints" in combined or "All values of parameter" in combined:
        return PictFailureCategory.OVER_CONSTRAINED
    if _looks_like_model_error(combined):
        return PictFailureCategory.MODEL_SYNTAX_ERROR
    if _looks_like_constraint_error(combined):
        return PictFailureCategory.CONSTRAINT_PARSE_ERROR
    return PictFailureCategory.MODEL_SYNTAX_ERROR


def _to_wsl_path(path: str) -> str:
    """把 Windows 路径（D:\\a\\b）转换为 WSL 路径（/mnt/d/a/b）。"""
    norm = os.path.abspath(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):(.*)$", norm)
    if m:
        return "/mnt/{}{}".format(m.group(1).lower(), m.group(2))
    return norm

def _find_windows_pict_candidates(max_results: int = 8, max_depth: int = 4) -> List[str]:
    """在常见位置浅层搜索 pict.exe（Windows 未安装到 PATH 时的兜底）。"""
    roots = []
    home = os.path.expanduser("~")
    roots.append(home)
    for letter in range(ord('C'), ord('Z') + 1):
        drive = "{}:\\".format(chr(letter))
        if os.path.exists(drive):
            roots.append(drive)
    found: List[str] = []
    seen = set()
    _SKIP = {
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information", "node_modules", ".git",
        "__pycache__", "msocache",
    }
    for root in roots:
        if len(found) >= max_results:
            break
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth > max_depth:
                dirnames[:] = []
                continue
            keep = [d for d in dirnames if d.lower() not in _SKIP]
            dirnames[:] = keep
            for fn in filenames:
                if fn.lower() == "pict.exe":
                    p = os.path.join(dirpath, fn)
                    if p not in seen:
                        seen.add(p)
                        found.append(p)
                    if len(found) >= max_results:
                        break
            if len(found) >= max_results:
                break
    return found


def _detect_wsl_distro() -> Optional[str]:
    """探测本机默认 WSL 发行版名称。"""
    try:
        r = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, text=True,
                           timeout=15, encoding="utf-8", errors="replace")
        text = (r.stdout or "").replace("\x00", "")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.lower().startswith(("microsoft", "windows", "docker", "---")):
                return line
    except Exception:
        pass
    return None


def _wsl_has_pict(distro: str) -> bool:
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", distro, "--", "bash", "-lc", "command -v pict"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        return r.returncode == 0 and "pict" in (r.stdout or "")
    except Exception:
        return False


def resolve_pict_command(pict_exe: Optional[str] = None, wsl_distro: Optional[str] = None,
                         search_local: bool = True) -> Optional[PictCommand]:
    """解析 PICT 可执行命令。

    优先级：显式 pict_exe > 环境变量 PICT_EXE > PATH >
    [显式 wsl_distro 时] WSL > 自动 WSL（快速探测）> 本地全盘搜索兜底。
    Linux 直接使用 `pict`；Windows 下先确认 WSL 中可用的 pict（探测快且可靠），
    全盘搜索（os.walk 遍历各盘）代价高，仅作为最后兜底。
    """
    cur = sys.platform
    explicit = []
    if pict_exe:
        explicit.append(pict_exe)
    env_exe = os.environ.get("PICT_EXE")
    if env_exe:
        explicit.append(env_exe)
    for c in explicit:
        if os.path.isfile(c):
            return PictCommand([c], c, cur)
        if cur != "win32" and os.access(c, os.X_OK):
            return PictCommand([c], c, cur)

    names = ["pict.exe", "pict"] if cur == "win32" else ["pict"]
    for n in names:
        p = shutil.which(n)
        if p:
            return PictCommand([p], p, cur)

    if cur == "win32":
        # 显式指定 WSL 发行版时，优先使用 WSL 中的 pict
        if wsl_distro and _wsl_has_pict(wsl_distro):
            return PictCommand(
                ["wsl.exe", "-d", wsl_distro, "--", "pict"],
                "{}:/pict".format(wsl_distro), cur, via_wsl=True, wsl_distro=wsl_distro,
            )
        # 自动探测 WSL（快），优先于全盘搜索
        distro = wsl_distro or _detect_wsl_distro()
        if distro and _wsl_has_pict(distro):
            return PictCommand(
                ["wsl.exe", "-d", distro, "--", "pict"],
                "{}:/pict".format(distro), cur, via_wsl=True, wsl_distro=distro,
            )
        if search_local:
            found = _find_windows_pict_candidates()
            if found:
                return PictCommand([found[0]], found[0], cur)
    return None


_PROBE_MODEL = "p1: 1, 2\np2: x, y\n"


def check_pict_environment(pict_exe: Optional[str] = None, wsl_distro: Optional[str] = None,
                           timeout: int = 10) -> PictEnvironmentReport:
    """探测当前环境 PICT 是否可用，并给出具体问题与修复建议。

    Windows 侧：pict.exe 存在但缺 32 位 VC++ 运行时(MSVCP140/VCRUNTIME140)时，
    分类为 launch_failure 并输出精确诊断；同时给出 WSL 备选建议。
    """
    cur = sys.platform
    cmd = resolve_pict_command(pict_exe=pict_exe, wsl_distro=wsl_distro)
    if cmd is None:
        diags = ["No pict / pict.exe found on PATH, PICT_EXE, or common locations."]
        sugg = []
        if cur == "win32":
            sugg.append("Install Microsoft Visual C++ 2015-2022 Redistributable (x86), then place pict.exe on PATH or set PICT_EXE.")
            sugg.append("Or debug on WSL: `wsl -d Ubuntu -- pict` is available if the WSL distro has pict installed.")
        else:
            sugg.append("Install PICT via `brew install pict` or build from source: `cmake -S . -B build && cmake --build build`.")
        return PictEnvironmentReport(
            available=False, command=None, platform=cur,
            failure_category=PictFailureCategory.PICT_NOT_FOUND,
            diagnostics=diags, suggestions=sugg,
        )

    probe_path = ""
    try:
        fd, probe_path = tempfile.mkstemp(suffix=".txt", prefix="pict_probe_", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_PROBE_MODEL)
        full = _build_pict_cmd(cmd, probe_path, strength=2, seed=None)
        logger.info("probe pict command: %s", " ".join(full))
        kwargs: Dict[str, Any] = dict(capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        if cur == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(full, **kwargs)
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        category = _classify_failure(res.returncode, combined, cmd)
        if category == PictFailureCategory.SUCCESS:
            return PictEnvironmentReport(
                available=True, command=" ".join(full), platform=cur,
                failure_category=PictFailureCategory.SUCCESS,
                probe_stdout=res.stdout or "", probe_stderr=res.stderr or "",
            )
        diags = ["PICT executable found but failed to run.",
                 "command: {}".format(" ".join(full)),
                 "exit code: {} ({})".format(res.returncode, _norm_exit_code(res.returncode))]
        if res.stderr and res.stderr.strip():
            diags.append("stderr: {}".format(res.stderr.strip()[:500]))
        sugg = []
        if category == PictFailureCategory.LAUNCH_FAILURE:
            dll_diags = _windows_dll_diagnostics()
            diags.extend(dll_diags)
            sugg.append("Install Microsoft Visual C++ 2015-2022 Redistributable (x86): run vc_redist.x86.exe (needs admin).")
            sugg.append("Or use a statically-linked pict build / run on WSL instead.")
            distro = wsl_distro or _detect_wsl_distro()
            if distro and _wsl_has_pict(distro):
                sugg.append("PICT is available via WSL: `wsl.exe -d {} -- pict`. Set PICT_EXE or use --wsl-distro {}.".format(distro, distro))
        elif category == PictFailureCategory.TIMEOUT:
            sugg.append("Increase probe --timeout.")
        else:
            sugg.append("Check the model/syntax or use a known-good PICT build.")
        return PictEnvironmentReport(
            available=False, command=" ".join(full), platform=cur,
            failure_category=category, diagnostics=diags, suggestions=sugg,
            probe_stdout=res.stdout or "", probe_stderr=res.stderr or "",
        )
    except subprocess.TimeoutExpired:
        return PictEnvironmentReport(
            available=False, command=" ".join(cmd.cmd_list), platform=cur,
            failure_category=PictFailureCategory.TIMEOUT,
            diagnostics=["Probe run timed out after {}s.".format(timeout)],
            suggestions=["Increase probe --timeout, or the pict process hangs on this model."],
        )
    except FileNotFoundError as exc:
        return PictEnvironmentReport(
            available=False, command=" ".join(cmd.cmd_list), platform=cur,
            failure_category=PictFailureCategory.PICT_NOT_FOUND,
            diagnostics=["Failed to spawn PICT: {}".format(exc)],
            suggestions=["Ensure pict executable path is correct."],
        )
    finally:
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass


def _windows_dll_diagnostics() -> List[str]:
    """检测 Windows 32 位 VC++ 运行时缺失情况（PICT 为 32 位 exe）。"""
    diags = []
    syswow = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64")
    for dll in ("vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"):
        p = os.path.join(syswow, dll)
        if not os.path.exists(p):
            diags.append("Missing 32-bit runtime DLL: {}".format(p))
    if not diags:
        diags.append("SysWOW64 VC++ runtime DLLs appear present; check other launch blockers.")
    return diags


def _build_pict_cmd(cmd: PictCommand, model_path: str, strength: int, seed: Optional[int]) -> List[str]:
    args = list(cmd.cmd_list)
    model_arg = _to_wsl_path(model_path) if cmd.via_wsl else model_path
    args.append(model_arg)
    args.append("/o:{}".format(strength))
    if seed is not None:
        args.append("/r:{}".format(seed))
    return args


@track("_run_pict")
def _run_pict(cmd: PictCommand, model_path: str, strength: int, seed: Optional[int],
              timeout: int) -> Tuple[Optional[subprocess.CompletedProcess], float]:
    full = _build_pict_cmd(cmd, model_path, strength, seed)
    logger.info("run pict: %s", " ".join(full))
    kwargs: Dict[str, Any] = dict(capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    start = time.perf_counter()
    res = subprocess.run(full, **kwargs)
    return res, time.perf_counter() - start


# --------------------------------------------------------------------------- #
# PICT 值格式化与模型文本
# --------------------------------------------------------------------------- #

def _is_scalar(v: Any) -> bool:
    return isinstance(v, (bool, int, float, str)) and not isinstance(v, (list, dict, tuple))


# PICT 值列表中的值必须不包含逗号/冒号/空格/引号等特殊字符，
# 且不能用双引号包裹（PICT 会把值列表中的引号当作值的一部分，
# 从而与约束里的字符串字面量不匹配）。
_SAFE_VALUE_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _format_pict_value(v: Any) -> Optional[str]:
    """把标量值格式化为 PICT 值列表中的 token（不加引号）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        if _SAFE_VALUE_TOKEN.match(v):
            return v
        return None
    return None


def _format_constraint_value(v: Any) -> Optional[str]:
    """把标量值格式化为 PICT 约束中的字面量（字符串/布尔加引号）。"""
    if isinstance(v, bool):
        return '"true"' if v else '"false"'
    if isinstance(v, str):
        return '"{}"'.format(v.replace('"', "'"))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return None


def _build_model_text(parameters: Dict[str, List[Any]], pict_constraints: List[str]) -> str:
    lines: List[str] = []
    for col, values in parameters.items():
        parts = [_format_pict_value(v) for v in values if _format_pict_value(v) is not None]
        if parts:
            lines.append("{}: {}".format(col, ", ".join(parts)))
    if pict_constraints:
        lines.append("")
        lines.extend(pict_constraints)
    return "\n".join(lines) + "\n"


# ---- AST 约束翻译 ---------------------------------------------------------- #

_PICT_CMP = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

_PICT_CMP_INVERT = {
    ast.Eq: ast.Eq,
    ast.NotEq: ast.NotEq,
    ast.Lt: ast.Gt,
    ast.LtE: ast.GtE,
    ast.Gt: ast.Lt,
    ast.GtE: ast.LtE,
}


def _operand_column(node: ast.AST) -> Optional[str]:
    """若操作数是属性引用（如 x.dtype），返回其列名（x_dtype）。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return "{}_{}".format(node.value.id, node.attr)
    return None


def _translate_operand(node: ast.AST, columns: set, warnings: List[str]) -> Optional[str]:
    """把常量/属性引用操作数翻译为 PICT 操作数。

    裸标识符（如 `x.dtype == FLOAT32` 中的 FLOAT32）在约束 evaluate 语义里是
    未定义变量（EvalState.UNKNOWN），因此不参与 PICT 翻译，返回 None。
    """
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return None
        fmt = _format_constraint_value(v)
        return fmt
    if isinstance(node, ast.Attribute):
        param = getattr(node.value, "id", None)
        if param is None or not isinstance(node.value, ast.Name):
            return None
        col = "{}_{}".format(param, node.attr)
        if col not in columns:
            warnings.append("constraint references unknown column '{}'".format(col))
            return None
        return "[{}]".format(col)
    if isinstance(node, ast.Name):
        if node.id == "True":
            return '"true"'
        if node.id == "False":
            return '"false"'
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return repr(-node.operand.value)
    return None


def _translate_compare(node: ast.Compare, columns: set, warnings: List[str],
                       parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[str]:
    """翻译单个（或链式）比较为 PICT。

    PICT 要求列引用在比较符左侧（`0 <= [a]` 会被当作参数声明解析失败），
    因此常量为左的操作数会被交换并反转比较符（`0 <= [a]` -> `[a] >= 0`）。
    链式比较 `a <= x <= b` 展开为 `([x] >= a) AND ([x] <= b)`。
    两个操作数都是列引用（跨参数比较，如 `x.dtype == weight.dtype`）无法用
    PICT 表达（PICT 只支持 `[param] op value`），直接判为不可翻译。
    """
    parameters = parameters or {}
    if len(node.ops) > 1:
        parts = []
        operands = [node.left] + list(node.comparators)
        for i, op in enumerate(node.ops):
            sub = ast.Compare(left=operands[i], ops=[op], comparators=[operands[i + 1]])
            r = _translate_compare(sub, columns, warnings, parameters)
            if r is None:
                return None
            parts.append("({})".format(r))
        return " AND ".join(parts)
    op = node.ops[0]
    if isinstance(op, (ast.In, ast.NotIn)):
        left = _translate_operand(node.left, columns, warnings)
        if left is None:
            return None
        vals = _translate_value_set(node.comparators[0], columns, warnings)
        if vals is None:
            return None
        body = "{} in {{{}}}".format(left, vals)
        return body if not isinstance(op, ast.NotIn) else "NOT ({})".format(body)
    op_sym = _PICT_CMP.get(type(op))
    if op_sym is None:
        warnings.append("unsupported comparison operator")
        return None
    left_node = node.left
    right_node = node.comparators[0]
    left_col = _operand_column(left_node)
    right_col = _operand_column(right_node)
    # 跨参数比较：两边都是列引用，PICT 不支持，剔除
    if left_col is not None and right_col is not None:
        warnings.append(
            "cross-parameter comparison '[{}] {} [{}]' not expressible in PICT".format(left_col, op_sym, right_col))
        return None
    # PICT 要求列在左侧；常量在左时交换并反转比较符
    if left_col is None and right_col is not None:
        left_node, right_node = right_node, left_node
        op_sym = _PICT_CMP[_PICT_CMP_INVERT[type(op)]]
    left = _translate_operand(left_node, columns, warnings)
    right = _translate_operand(right_node, columns, warnings)
    if left is None or right is None:
        return None
    return "{} {} {}".format(left, op_sym, right)


def _common_values(left_col: str, right_col: str,
                   parameters: Dict[str, List[Any]]) -> List[Any]:
    """返回两列取值域的交集（保持 left 列顺序，用 repr 去重）。"""
    parameters = parameters or {}
    left_vals = parameters.get(left_col, [])
    right_vals = parameters.get(right_col, [])
    right_reprs = {repr(v) for v in right_vals}
    common: List[Any] = []
    seen = set()
    for v in left_vals:
        key = repr(v)
        if key in right_reprs and key not in seen:
            seen.add(key)
            common.append(v)
    return common


def _expand_cross_equality(left_col: str, right_col: str,
                           parameters: Dict[str, List[Any]],
                           warnings: List[str]) -> Optional[List[str]]:
    """把跨参数相等 `[left_col] == [right_col]` 展开为值枚举的 IF-THEN 约束。

    PICT 只支持 `[param] op value`，无法表达 `[a] = [b]`，因此对两列取值交集中的
    每个公共值 v 生成 `IF [a]=v THEN [b]=v;`（单向）。当值域一致时单向枚举即可
    完全表达相等（a 取任意值都被强制等于 b）；值域不对称或链式比较的传递性缺口，
    由后续约束二次过滤兜底。
    """
    parameters = parameters or {}
    common = _common_values(left_col, right_col, parameters)
    if not common:
        warnings.append(
            "cross-parameter equality '[{}] = [{}]' has no common value, dropped".format(left_col, right_col))
        return None
    lines: List[str] = []
    for v in common:
        fv = _format_constraint_value(v)
        if fv is None:
            continue
        lines.append("IF [{}] = {} THEN [{}] = {};".format(left_col, fv, right_col, fv))
    if not lines:
        warnings.append(
            "cross-parameter equality '[{}] = [{}]' values not formattable, dropped".format(left_col, right_col))
        return None
    return lines


def _translate_value_set(node: ast.AST, columns: set, warnings: List[str]) -> Optional[str]:
    """把 `in [...]` 右侧的列表翻译为 PICT `{v1, v2, ...}`。"""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    parts = []
    for elem in node.elts:
        v = _translate_operand(elem, columns, warnings)
        if v is None:
            return None
        parts.append(v)
    if not parts:
        return None
    return ", ".join(parts)


def _translate_relation(node: ast.AST, columns: set, warnings: List[str],
                        parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[str]:
    """把表达式翻译为 PICT 关系串（支持 AND/OR/NOT，不含 IF THEN）。

    扩展支持：
    - 裸布尔属性引用 `x.is_present` -> `[x_is_present] = "true"`；
    - `not x.is_present` -> `[x_is_present] = "false"`；
    - AND 中局部不可翻译的子项被剔除（OR 保持严格，避免语义破坏）。
    """
    if isinstance(node, ast.Attribute):
        col = _operand_column(node)
        if col is None or col not in columns:
            warnings.append("constraint references unknown column '{}'".format(col))
            return None
        return '[{}] = "true"'.format(col)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, ast.Attribute):
            col = _operand_column(node.operand)
            if col is None or col not in columns:
                warnings.append("constraint references unknown column '{}'".format(col))
                return None
            return '[{}] = "false"'.format(col)
        inner = _translate_relation(node.operand, columns, warnings, parameters)
        if inner is None:
            return None
        return "NOT ({})".format(inner)
    if isinstance(node, ast.Compare):
        return _translate_compare(node, columns, warnings, parameters)
    if isinstance(node, ast.BoolOp):
        parts = []
        for v in node.values:
            p = _translate_relation(v, columns, warnings, parameters)
            if p is None:
                if isinstance(node.op, ast.And):
                    warnings.append("dropped untranslatable AND sub-term")
                    continue
                return None
            parts.append("({})".format(p))
        if not parts:
            return None
        sep = " AND " if isinstance(node.op, ast.And) else " OR "
        return sep.join(parts)
    if isinstance(node, ast.Call):
        return _translate_anyall(node, columns, warnings, parameters)
    return None


def _translate_condition(node: ast.AST, columns: set, warnings: List[str],
                         parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[str]:
    """三元表达式 test 分支翻译（同样支持 AND/OR/NOT 的守卫）。"""
    return _translate_relation(node, columns, warnings, parameters)


def _collect_ternary(node: ast.AST, guard_terms: List[str], columns: set,
                     warnings: List[str], parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    """把 `A if C else B`（可嵌套）展开为多个 `IF guard THEN concl;` 行。

    guard 是 PICT 支持的合取条件（含 NOT），例如 `(C) AND (NOT C2)`。
    """
    if isinstance(node, ast.IfExp):
        cond = _translate_condition(node.test, columns, warnings, parameters)
        if cond is None:
            return None
        pos = guard_terms + ["({})".format(cond)]
        neg = guard_terms + ["(NOT ({}))".format(cond)]
        left = _collect_ternary(node.body, pos, columns, warnings, parameters)
        if left is None:
            return None
        right = _collect_ternary(node.orelse, neg, columns, warnings, parameters)
        if right is None:
            return None
        return left + right
    # 叶子：比较/逻辑关系，或 True/False 字面量
    if isinstance(node, ast.Constant) and node.value is True:
        return []
    if isinstance(node, ast.Constant) and node.value is False:
        warnings.append("boolean False conclusion cannot be expressed in PICT")
        return None
    concl = _translate_relation(node, columns, warnings, parameters)
    if concl is None:
        return None
    if not guard_terms:
        return ["({});".format(concl)]
    return ["IF {} THEN ({});".format(" AND ".join(guard_terms), concl)]


def _collect_or_term(node: ast.AST, columns: set, warnings: List[str],
                     parameters: Dict[str, List[Any]],
                     not_guards: List[str], neg_guards: List[str],
                     cross_eqs: List[Tuple[str, str]]) -> bool:
    """递归拆 And，提取其中的跨参相等/not/普通项；不可翻译子项（如算术）局部剔除。

    返回是否有任何产出（True=至少提取了一项）。
    """
    produced = [False]

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.And):
            for sub in n.values:
                walk(sub)
            return
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            inner = _translate_relation(n.operand, columns, warnings, parameters)
            if inner is not None:
                not_guards.append(inner)
                produced[0] = True
            return
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Eq):
            left_col = _operand_column(n.left)
            right_col = _operand_column(n.comparators[0])
            if left_col is not None and right_col is not None:
                cross_eqs.append((left_col, right_col))
                produced[0] = True
                return
        r = _translate_relation(n, columns, warnings, parameters)
        if r is not None:
            neg_guards.append(r)
            produced[0] = True

    walk(node)
    return produced[0]


def _translate_or(node: ast.BoolOp, columns: set, warnings: List[str],
                  parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    """把顶层 `... or ...` 翻译为 PICT。

    - 不含跨参数相等：`(a) OR (b)`，或含单个 `not A` 时转蕴含 `IF (A) THEN (rest);`；
    - 含跨参数相等：转蕴含并把跨参相等按公共值枚举展开进 THEN，
      例如 `(A == False) or (x.length == weight.length)` ->
      `IF NOT ([A] = "false") AND [x_length] = v THEN [weight_length] = v;`（双向）。
    """
    parameters = parameters or {}
    not_guards: List[str] = []
    neg_guards: List[str] = []
    cross_eqs: List[Tuple[str, str]] = []
    for v in node.values:
        if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not):
            inner = _translate_relation(v.operand, columns, warnings, parameters)
            if inner is None:
                return None
            not_guards.append(inner)
            continue
        if isinstance(v, ast.Compare) and len(v.ops) == 1 and isinstance(v.ops[0], ast.Eq):
            left_col = _operand_column(v.left)
            right_col = _operand_column(v.comparators[0])
            if left_col is not None and right_col is not None:
                cross_eqs.append((left_col, right_col))
                continue
        # 优先整体翻译（_translate_relation 会局部剔除不可翻译项）
        r = _translate_relation(v, columns, warnings, parameters)
        if r is not None:
            neg_guards.append(r)
            continue
        # 整体失败：若为 And，递归提取其中的跨参相等
        if isinstance(v, ast.BoolOp) and isinstance(v.op, ast.And):
            if _collect_or_term(v, columns, warnings, parameters,
                                not_guards, neg_guards, cross_eqs):
                continue
        return None

    if not cross_eqs:
        if not not_guards:
            return ["({});".format(" OR ".join("({})".format(p) for p in neg_guards))]
        if len(not_guards) == 1 and neg_guards:
            rest = " OR ".join("({})".format(p) for p in neg_guards)
            return ["IF ({}) THEN ({});".format(not_guards[0], rest)]
        warnings.append("multiple NOT or bare NOT clauses unsupported")
        return None

    # 含跨参相等：guard = 正向 not 子项 + 普通子项取反
    guards = ["({})".format(p) for p in not_guards] + ["(NOT ({}))".format(r) for r in neg_guards]
    if not guards:
        warnings.append("cross-parameter equality OR without any guard cannot be expressed")
        return None
    # PICT 对「in 集合 guard 的 3-way IF-THEN」求解会爆炸（实测 >180s 无结果），
    # 这类约束不展开，交二次约束过滤兜底；单值/NOT guard 的 3-way 仍是快速的。
    if any(" in {" in g for g in guards):
        warnings.append(
            "cross-parameter equality OR with 'in' guard is too expensive for PICT; dropped")
        return None
    # 共享左列的多个跨参相等（如 key==value and key==keyCacheRef）展开后约束网络复杂，
    # PICT 求解爆炸（实测 >50s），这类不展开，交二次约束过滤兜底。
    left_cols = [lc for lc, _ in cross_eqs]
    if len(left_cols) != len(set(left_cols)):
        warnings.append(
            "cross-parameter equality OR with shared left column is too expensive for PICT; dropped")
        return None
    guard_str = " AND ".join(guards)

    lines: List[str] = []
    for left_col, right_col in cross_eqs:
        common = _common_values(left_col, right_col, parameters)
        if not common:
            warnings.append(
                "cross-parameter equality '[{}] = [{}]' has no common value, dropped".format(left_col, right_col))
            continue
        for v in common:
            fv = _format_constraint_value(v)
            if fv is None:
                continue
            lines.append("IF {} AND [{}] = {} THEN [{}] = {};".format(
                guard_str, left_col, fv, right_col, fv))
    return lines or None


def _translate_anyall(node: ast.Call, columns: set, warnings: List[str],
                      parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    func = node.func
    if not isinstance(func, ast.Name) or func.id not in ("any", "all"):
        warnings.append("unsupported function call '{}'".format(getattr(func, "id", "?")))
        return None
    if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
        return None
    parts = []
    for elem in node.args[0].elts:
        p = _translate_relation(elem, columns, warnings, parameters)
        if p is None:
            return None
        parts.append("({})".format(p))
    sep = " OR " if func.id == "any" else " AND "
    return ["({});".format(sep.join(parts))]


def _is_relational_ast(node: ast.AST) -> bool:
    return isinstance(node, (ast.Compare, ast.BoolOp)) or (
        isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)) or isinstance(node, ast.Call)


def _translate_compare_expr(node: ast.Compare, columns: set, warnings: List[str],
                            parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    """把比较表达式翻译为 0..n 条 PICT 约束行（含跨参数相等展开）。"""
    parameters = parameters or {}
    # 链式比较 `a op b op c`：拆成相邻两两比较（AND 语义）
    if len(node.ops) > 1:
        operands = [node.left] + list(node.comparators)
        lines: List[str] = []
        for i, op in enumerate(node.ops):
            sub = ast.Compare(left=operands[i], ops=[op], comparators=[operands[i + 1]])
            sub_lines = _translate_compare_expr(sub, columns, warnings, parameters)
            if sub_lines is None:
                return None
            lines.extend(sub_lines)
        return lines
    op = node.ops[0]
    left_col = _operand_column(node.left)
    right_col = _operand_column(node.comparators[0])
    # 跨参数比较：两边都是列引用
    if left_col is not None and right_col is not None:
        if isinstance(op, ast.Eq):
            return _expand_cross_equality(left_col, right_col, parameters, warnings)
        warnings.append(
            "cross-parameter comparison '[{}] {} [{}]' not expressible in PICT".format(
                left_col, _PICT_CMP.get(type(op), "?"), right_col))
        return None
    # 等价/蕴含：Eq/NotEq 且两边都是关系表达式
    if isinstance(op, (ast.Eq, ast.NotEq)) \
            and _is_relational_ast(node.left) and _is_relational_ast(node.comparators[0]):
        left_rel = _translate_relation(node.left, columns, warnings, parameters)
        right_rel = _translate_relation(node.comparators[0], columns, warnings, parameters)
        if left_rel is not None and right_rel is not None:
            if isinstance(op, ast.Eq):
                return ["IF ({}) THEN ({});".format(left_rel, right_rel),
                        "IF ({}) THEN ({});".format(right_rel, left_rel)]
            return ["IF ({}) THEN (NOT ({}));".format(left_rel, right_rel),
                    "IF ({}) THEN (NOT ({}));".format(right_rel, left_rel)]
        return None
    # 普通关系（含 In/NotIn、常量比较）
    r = _translate_compare(node, columns, warnings, parameters)
    return ["({});".format(r)] if r is not None else None


def _translate_and_term(node: ast.AST, columns: set, warnings: List[str],
                        parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    """翻译 AND 的单个子项为 0..n 条约束行。"""
    if isinstance(node, ast.Compare):
        return _translate_compare_expr(node, columns, warnings, parameters)
    if isinstance(node, ast.BoolOp):
        return _translate_expr(node, columns, warnings, parameters)
    if isinstance(node, ast.Call):
        return _translate_anyall(node, columns, warnings, parameters)
    r = _translate_relation(node, columns, warnings, parameters)
    return ["({});".format(r)] if r is not None else None


def _translate_expr(node: ast.AST, columns: set, warnings: List[str],
                    parameters: Optional[Dict[str, List[Any]]] = None) -> Optional[List[str]]:
    """把一条 Python 约束表达式翻译为 0..n 条 PICT 约束行（均已带分号）。"""
    if isinstance(node, ast.IfExp):
        return _collect_ternary(node, [], columns, warnings, parameters)
    if isinstance(node, ast.Compare):
        return _translate_compare_expr(node, columns, warnings, parameters)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.Or):
            return _translate_or(node, columns, warnings, parameters)
        # And：拆分为多条独立约束（PICT 多条约束之间为 AND 语义）
        lines: List[str] = []
        for v in node.values:
            sub = _translate_and_term(v, columns, warnings, parameters)
            if sub is None:
                warnings.append("dropped untranslatable AND sub-term")
                continue
            lines.extend(sub)
        return lines or None
    if isinstance(node, ast.Call):
        return _translate_anyall(node, columns, warnings, parameters)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        warnings.append("top-level NOT cannot be expressed as a bare PICT constraint")
        return None
    if isinstance(node, ast.Constant):
        if node.value is True:
            return []
        warnings.append("constant-only constraint unsupported")
        return None
    warnings.append("unsupported constraint expression")
    return None


def _translate_constraint(expr: str, columns: set,
                          parameters: Optional[Dict[str, List[Any]]] = None) -> Tuple[Optional[str], List[str]]:
    """翻译单条约束；返回 (pict_text 或 None, warnings)。None 表示应剔除。"""
    warnings: List[str] = []
    if not isinstance(expr, str) or not expr.strip():
        return None, ["empty constraint"]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return None, ["python syntax error: {}".format(exc)]
    lines = _translate_expr(tree.body, columns, warnings, parameters)
    if lines is None:
        return None, warnings
    if not lines:
        return "", warnings
    return "\n".join(lines), warnings


# --------------------------------------------------------------------------- #
# PICT 输入转换（domain_data dict -> model txt）
# --------------------------------------------------------------------------- #

def convert_domain_json_to_pict_model(operator_name:str,
    domain_data: Dict,
    output_txt_path: Optional[str] = None,
) -> PictModel:
    """把 domain_data dict 直接转换为 PICT 可接受的 model txt。

    输入格式：{ "parameters": {param: {attr: [values]}}, "constraints": [expr, ...] }。
    参数被展平为 {param}_{attr} 列；为保证 model.txt 绝对合法：
    - 空值域属性 / 非标量取值（null、list 等）被剔除，剔除后为空的列整体跳过；
    - 字符串值不加引号（PICT 值列表中的引号会被当作值的一部分）；
    - 约束经 AST 尽力翻译为合法 PICT 语法，无法翻译的约束原地剔除并记录 warning。

    保存位置由 output_txt_path 指定。
    """

    raw_parameters = domain_data.get("parameters")
    if not isinstance(raw_parameters, dict):
        raise ValueError("'{}' has no 'parameters' dict".format(operator_name))

    parameters: Dict[str, List[Any]] = {}
    col_map: Dict[str, Tuple[str, str]] = {}
    columns: List[str] = []
    warnings: List[str] = []

    for param, attrs in raw_parameters.items():
        if not isinstance(attrs, dict):
            warnings.append("parameter '{}' attributes is not a dict, skipped".format(param))
            continue
        for attr, values in attrs.items():
            if not isinstance(values, list):
                continue
            col = "{}_{}".format(param, attr)
            col_vals: List[Any] = []
            for v in values:
                if not _is_scalar(v):
                    if v is not None:
                        warnings.append(
                            "non-scalar value skipped in {}.{}: {}".format(param, attr, repr(v)[:60]))
                    continue
                if v not in col_vals:
                    col_vals.append(v)
            if col_vals:
                parameters[col] = col_vals
                col_map[col] = (param, attr)
                columns.append(col)
            else:
                warnings.append("empty column skipped: {}".format(col))

    constraints = domain_data.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    constraints = [c for c in constraints if isinstance(c, str)]

    pict_constraints: List[str] = []
    dropped_constraints: List[str] = []
    col_set = set(columns)
    for c in constraints:
        translated, ws = _translate_constraint(c, col_set, parameters)
        if translated is None:
            dropped_constraints.append(c)
            warnings.extend(ws or ["untranslatable constraint dropped: {}".format(c[:80])])
        elif translated:
            for line in translated.splitlines():
                if line.strip():
                    pict_constraints.append(line)

    model_text = _build_model_text(parameters, pict_constraints)

    if output_txt_path:
        out = os.path.abspath(output_txt_path)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(model_text)
        logger.info("wrote PICT model (%d columns, %d constraints, %d dropped) -> %s",
                    len(columns), len(pict_constraints), len(dropped_constraints), out)

    return PictModel(
        model_text=model_text,
        columns=columns,
        parameters=parameters,
        col_map=col_map,
        constraints=constraints,
        pict_constraints=pict_constraints,
        dropped_constraints=dropped_constraints,
        warnings=warnings,
        operator_name=operator_name,
    )


# --------------------------------------------------------------------------- #
# PICT 执行（失败分类 + 多轮非法约束剔除 + 结果落盘）
# --------------------------------------------------------------------------- #

def _split_model(model_text: str) -> Tuple[List[str], List[str]]:
    """把 model 文本拆分为参数行与约束行。"""
    param_lines: List[str] = []
    cons_lines: List[str] = []
    in_params = True
    for raw in model_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            in_params = False
            continue
        if in_params and re.match(r"^\s*[^:\n]+\s*:", line):
            param_lines.append(line)
        else:
            cons_lines.append(line)
    return param_lines, cons_lines


def _rebuild_model(param_lines: List[str], constraints: List[str]) -> str:
    lines = list(param_lines)
    if constraints:
        lines.append("")
        lines.extend(constraints)
    return "\n".join(lines) + "\n"


def _match_offending(combined: str, constraints: List[str]) -> Optional[int]:
    """从 PICT 报错文本中定位被点名的非法约束下标。"""
    combined = _clean_output(combined)
    if not constraints:
        return None
    for idx, c in enumerate(constraints):
        key = c.strip()
        if key and key in combined:
            return idx
    best = None
    best_score = 0
    token_re = re.compile(r"\[[A-Za-z_][\w]*\]|\"[^\"]*\"")
    for idx, c in enumerate(constraints):
        tokens = token_re.findall(c)
        score = sum(1 for t in tokens if t in combined)
        if score > best_score:
            best_score = score
            best = idx
    return best


def _coerce_value(v: str) -> Any:
    v = v.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    if v == "true":
        return True
    if v == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _parse_tsv(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """解析 PICT TSV 输出（跳过 Constraints Warning / Input Error 等非表格行）。"""
    data = [l for l in text.splitlines() if "\t" in l]
    if not data:
        return [], []
    headers = [h.strip() for h in data[0].split("\t")]
    cases: List[Dict[str, Any]] = []
    for line in data[1:]:
        cells = line.split("\t")
        case: Dict[str, Any] = {}
        for i, h in enumerate(headers):
            if i < len(cells):
                case[h] = _coerce_value(cells[i])
        cases.append(case)
    return cases, headers


def _resolve_result_base(result_output_path: str, default_stem: str) -> str:
    p = os.path.abspath(result_output_path)
    if p.endswith(os.sep) or p.endswith("/") or os.path.isdir(p):
        os.makedirs(p, exist_ok=True)
        return os.path.join(p, default_stem)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    stem = os.path.splitext(os.path.basename(p))[0] or default_stem
    return os.path.join(parent, stem)


def _write_json(path: str, data: Any) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _dataclass_to_dict(obj: Any) -> Any:
    return asdict(obj)

@track("execute_pict")
def execute_pict(
    operator_name: str,
    model: Union[str, PictModel],
    result_output_path: str,
    *,
    strength: int = 2,
    seed: Optional[int] = None,
    timeout: int = 60,
    max_rounds: int = 20,
    pict_exe: Optional[str] = None,
    wsl_distro: Optional[str] = None,
    write_artifacts: bool = True,
) -> PictRunReport:
    """执行 PICT；失败分类并日志记录；非法约束多轮剔除；结果保存到 result_output_path。

    result_output_path 为目录或文件路径；产出的结果三件套：
        {base}_raw.tsv / {base}_cases.json / {base}_report.json
    另按轮保存 {base}_round_NN_model.txt / {base}_round_NN_stderr.txt 留痕。
    """
    started = time.perf_counter()
    base = ""
    rounds_dir: Optional[str] = None
    try:
        if isinstance(model, PictModel):
            model_text = model.model_text
        else:
            with open(model, encoding="utf-8") as f:
                model_text = f.read()
        base = _resolve_result_base(result_output_path, "{}_combination_data.json".format(operator_name))
        if write_artifacts:
            rounds_dir = os.path.dirname(base) or "."
        else:
            rounds_dir = tempfile.mkdtemp(prefix="pict_exec_")

        cmd = resolve_pict_command(pict_exe=pict_exe, wsl_distro=wsl_distro)
        if cmd is None:
            report = PictRunReport(success=False, failure_category=PictFailureCategory.PICT_NOT_FOUND)
            report.elapsed_time = time.perf_counter() - started
            return _finish_report(report, base, write_artifacts)

        param_lines, cons_lines = _split_model(model_text)
        remaining = list(cons_lines)
        rounds: List[PictRoundResult] = []
        removed: List[str] = []
        final_category = PictFailureCategory.MODEL_SYNTAX_ERROR
        success_stdout: Optional[str] = None
        final_model_text: Optional[str] = None

        for round_idx in range(max_rounds):
            current_text = _rebuild_model(param_lines, remaining)
            round_model_path = os.path.join(rounds_dir, "round_{:02d}_model.txt".format(round_idx))
            with open(round_model_path, "w", encoding="utf-8") as f:
                f.write(current_text)
            try:
                res, dt = _run_pict(cmd, round_model_path, strength, seed, timeout)
            except subprocess.TimeoutExpired:
                rounds.append(PictRoundResult(
                    round_index=round_idx, constraints_in_use=len(remaining),
                    failure_category=PictFailureCategory.TIMEOUT, returncode=None,
                    stderr="timeout after {}s".format(timeout), stdout=""))
                final_category = PictFailureCategory.TIMEOUT
                break

            combined = _clean_output((res.stdout or "") + "\n" + (res.stderr or ""))
            category = _classify_failure(res.returncode, combined, cmd)
            logger.info("pict round %d: rc=%s category=%s constraints=%d",
                        round_idx, res.returncode, category, len(remaining))
            cleaned_err = _clean_output(res.stderr or "")
            cleaned_out = _clean_output(res.stdout or "")
            round_result = PictRoundResult(
                round_index=round_idx, constraints_in_use=len(remaining),
                failure_category=category, returncode=res.returncode,
                stderr=cleaned_err, stdout=cleaned_out)
            rounds.append(round_result)

            if write_artifacts:
                _write_json("{}_round_{:02d}_stderr.txt".format(base, round_idx),
                            {"returncode": res.returncode, "category": category, "stderr": cleaned_err})

            if category == PictFailureCategory.SUCCESS:
                success_stdout = cleaned_out
                final_model_text = current_text
                final_category = PictFailureCategory.SUCCESS
                break

            if category in (PictFailureCategory.LAUNCH_FAILURE, PictFailureCategory.PICT_NOT_FOUND,
                            PictFailureCategory.TIMEOUT, PictFailureCategory.OVER_CONSTRAINED):
                final_category = category
                break

            if not remaining:
                final_category = category
                break

            offender = _match_offending(combined, remaining)
            if offender is None and category == PictFailureCategory.MODEL_SYNTAX_ERROR:
                final_category = category
                break
            if offender is None:
                offender = len(remaining) - 1
            removed_line = remaining.pop(offender)
            removed.append(removed_line)
            round_result.removed_constraint = removed_line
            logger.warning("pict round %d: dropped illegal constraint -> %s", round_idx, removed_line)
            final_category = PictFailureCategory.CONSTRAINT_PARSE_ERROR

        cases: List[Dict[str, Any]] = []
        if success_stdout is not None:
            cases, headers = _parse_tsv(success_stdout)
            if write_artifacts:
                with open("{}_raw.tsv".format(base), "w", encoding="utf-8") as f:
                    f.write(success_stdout)
                with open("{}_cases.json".format(base), "w", encoding="utf-8") as f:
                    json.dump(cases, f, ensure_ascii=False, indent=2)
                if final_model_text is not None:
                    with open("{}_model.txt".format(base), "w", encoding="utf-8") as f:
                        f.write(final_model_text)

        report = PictRunReport(
            success=success_stdout is not None,
            failure_category=PictFailureCategory.SUCCESS if success_stdout is not None else final_category,
            rounds=rounds,
            removed_constraints=removed,
            case_count=len(cases),
            elapsed_time=time.perf_counter() - started,
            model_path="{}_model.txt".format(base) if (success_stdout is not None and write_artifacts) else "",
            raw_output_path="{}_raw.tsv".format(base) if (success_stdout is not None and write_artifacts) else "",
            cases_output_path="{}_cases.json".format(base) if (success_stdout is not None and write_artifacts) else "",
            report_output_path="{}_report.json".format(base),
            success_stdout=success_stdout or "",
        )
        logger.info("pict finished: success=%s category=%s cases=%d rounds=%d removed=%d",
                    report.success, report.failure_category, report.case_count,
                    len(report.rounds), len(report.removed_constraints))
        return _finish_report(report, base, write_artifacts)
    except Exception as exc:
        logger.exception("execute_pict failed: %s", exc)
        report = PictRunReport(success=False, failure_category=PictFailureCategory.MODEL_SYNTAX_ERROR,
                               elapsed_time=time.perf_counter() - started)
        return _finish_report(report, base, write_artifacts)
    finally:
        if rounds_dir and not write_artifacts:
            shutil.rmtree(rounds_dir, ignore_errors=True)


def _finish_report(report: PictRunReport, base: str, write_artifacts: bool) -> PictRunReport:
    """写 report.json（含失败场景分类记录）并返回 report。"""
    try:
        report.report_output_path = "{}_report.json".format(base)
        if write_artifacts:
            data = _dataclass_to_dict(report)
            data.pop("success_stdout", None)
            _write_json(report.report_output_path, data)
    except Exception as exc:  # 报告落盘失败不影响主流程
        logger.exception("failed to write pict report: %s", exc)
    return report
