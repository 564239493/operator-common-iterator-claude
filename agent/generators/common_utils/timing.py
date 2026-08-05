"""
轻量级方法执行时间统计工具。

提供装饰器 ``@track`` 和上下文管理器 ``track_block``，支持：
- 单次调用计时
- 累计统计（调用次数、总耗时、平均耗时、最小/最大耗时）
- 汇总报告

用法示例::

    from pairwise.timing import track, track_block, print_summary

    @track
    def my_function():
        ...

    @track("水平增长")
    def horizontal_growth():
        ...

    with track_block("覆盖集重建"):
        rebuild()

    # 在程序末尾打印汇总
    print_summary()
"""

import functools
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from agent.generators.common_utils.logger_util import LazyLogger

logger = LazyLogger()


class _Stats(NamedTuple):
    count: int
    total: float
    min: float
    max: float


_registry: Dict[str, List[float]] = defaultdict(list)


def _record(label: str, elapsed: float) -> None:
    _registry[label].append(elapsed)


def record_time(label: str, elapsed: float) -> None:
    """公开接口：记录一次耗时，label 为统计标签。"""
    _record(label, elapsed)


def get_stats() -> Dict[str, _Stats]:
    """返回所有已记录标签的累计统计。"""
    result: Dict[str, _Stats] = {}
    for label in sorted(_registry):
        times = _registry[label]
        result[label] = _Stats(
            count=len(times),
            total=sum(times),
            min=min(times),
            max=max(times),
        )
    return result


def reset() -> None:
    """清空所有累计统计。"""
    _registry.clear()


def print_summary(file=None) -> None:
    """打印所有计时标签的汇总报告。

    输出格式::

        =============  Timing Summary  =============
        Label                    Count    Total(s)  Avg(ms)   Min(ms)   Max(ms)
        ─────────────────────────────────────────────────────────────────────
        name1                       42       1.234     29.38     10.50     89.12
        name2                       15       0.567     37.80     22.10     66.40
        =============================================
    """
    stats = get_stats()
    if not stats:
        logger.error(f"[timing] No timing data was recorded, file : {file}")
        return

    header = (
        f"{'Label':<50s} {'Count':>6s}  {'Total(s)':>9s}  "
        f"{'Avg(ms)':>9s}  {'Min(ms)':>9s}  {'Max(ms)':>9s}"
    )
    sep = "─" * len(header)

    lines: List[str] = [
        "=============  Timing Summary  =============",
        f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        header,
        sep,
    ]
    total_stats = stats.pop("TOTAL", None)
    for label, s in stats.items():
        total_s = s.total
        avg_ms = (total_s / s.count) * 1000
        min_ms = s.min * 1000
        max_ms = s.max * 1000
        lines.append(
            f"{label:<50s} {s.count:>6d}  {total_s:>9.3f}  "
            f"{avg_ms:>9.2f}  {min_ms:>9.2f}  {max_ms:>9.2f}"
        )

    if total_stats is not None:
        s = total_stats
        total_s = s.total
        avg_ms = (total_s / s.count) * 1000
        min_ms = s.min * 1000
        max_ms = s.max * 1000
        lines.append(
            f"{'TOTAL':<50s} {s.count:>6d}  {total_s:>9.3f}  "
            f"{avg_ms:>9.2f}  {min_ms:>9.2f}  {max_ms:>9.2f}"
        )

    lines.append("=" * len(header))

    out = "\n".join(lines)
    if file is not None:
        logger.debug(f"Time record to file : {out}, file : {file}")
        file.write(out)
        file.flush()
        file.close()
    else:
        logger.debug(f"Time record : {out}")


_track_stack = threading.local()


def track(label: Optional[str] = None) -> Callable:
    """记录被装饰函数每次调用的执行时间（独占时间，不含子函数）。

    通过线程局部调用栈，自动扣减嵌套子函数的耗时，
    使 sum(所有标签的 Total(s)) ≈ TOTAL 标签的 Total(s)。

    可作为带参数或不带参数的装饰器使用::

        @track
        def foo(): ...

        @track("水平增长")
        def bar(): ...

        @track(label="垂直增长")
        def baz(): ...

    Args:
        label: 计时标签。为 ``None`` 时自动使用 ``模块.函数名``。

    Returns:
        装饰器函数。
    """
    if label is not None and callable(label):
        return track(label=None)(label)

    def decorator(func: Callable) -> Callable:
        _label = label or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not hasattr(_track_stack, 'entries'):
                _track_stack.entries = []
            entries = _track_stack.entries

            if entries:
                entries[-1]['_paused'] = time.perf_counter()

            entry = {
                'label': _label,
                'start': time.perf_counter(),
                'accumulated': 0.0,
                '_paused': None,
            }
            entries.append(entry)

            try:
                return func(*args, **kwargs)
            finally:
                end = time.perf_counter()
                entry = entries.pop()
                wall = end - entry['start']
                exclusive = wall - entry['accumulated']
                _record(_label, exclusive)

                if entries:
                    parent = entries[-1]
                    parent['accumulated'] += wall

        return wrapper

    return decorator


@contextmanager
def track_block(label: str):
    """上下文管理器，用于统计代码块的执行时间。

    用法::

        with track_block("覆盖集重建"):
            rebuild_covered_set()

    Args:
        label: 计时标签。
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _record(label, time.perf_counter() - t0)
