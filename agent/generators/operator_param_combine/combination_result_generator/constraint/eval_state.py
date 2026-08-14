from enum import Enum

class EvalState(Enum):
    """三值逻辑：TRUE / FALSE / UNKNOWN。

    用于部分求值：
    - TRUE  : 已知值能确定约束满足
    - FALSE : 已知值能确定约束违反
    - UNKNOWN：缺失变量参与计算，无法判定

    __bool__ 设计：UNKNOWN 为 truthy，使 `if not evaluate(...)` 将 UNKNOWN
    当作"非 FALSE"→放过，等价原 try/except → continue 语义。
    """
    TRUE = True
    FALSE = False
    UNKNOWN = None

    def __bool__(self) -> bool:
        return self is not EvalState.FALSE