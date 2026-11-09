"""
Constraint Layer Exception Definition.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Design:
    All constraint related exceptions inherit
    ConstraintError.

Error Code Range:
    C2000-C2099
"""


from typing import Optional



# ============================================================
# Binding Failure Description
# ============================================================

# C2003 的四种根因，用于把“属性读不到”翻译成一句人话。
# 不翻译时消息只剩 "Attribute not found: dtype"，无法区分拼写错误与参数缺席。
CAUSE_LABELS = {
    "absent": "parameter absent in this row (mapped to None), attributes unreadable",
    "missing_key": "dict has no such key",
    "missing_attr": "object has no such attribute",
    "unbound": "name not bound in the evaluation context",
}


def describe_binding_cause(
    param: Optional[str],
    attr: Optional[str],
    cause: str,
) -> str:
    """把一次绑定失败的定位信息压成一句短语。

    求值侧与生成侧共用，保证逐条日志与汇总日志措辞一致、可按同一前缀 grep。

    :param param: 出问题的参数名，无法确定时为 None
    :param attr: 读取失败的属性名，无属性访问时为 None
    :param cause: 根因分类，取自 CAUSE_LABELS
    :return: 形如 ``param 'x' attr 'dtype' — <原因>`` 的单行描述
    """
    subject = f"param '{param}'" if param else "unbound name"
    if attr:
        subject += f" attr '{attr}'"
    return f"{subject} — {CAUSE_LABELS.get(cause, cause or 'unresolved')}"



# ============================================================
# Base Exception
# ============================================================


class ConstraintError(Exception):
    """
    Base exception of Constraint Layer.
    """


    ERROR_CODE = "C2000"


    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[str] = None,
    ) -> None:

        self.message = message

        self.error_code = (
            error_code
            or self.ERROR_CODE
        )


        super().__init__(
            f"[{self.error_code}] {self.message}"
        )


    def __str__(self) -> str:

        return (
            f"[{self.error_code}] "
            f"{self.message}"
        )



# ============================================================
# Syntax Exception
# C2001
# ============================================================


class ConstraintSyntaxError(ConstraintError):
    """
    Invalid constraint expression syntax.

    Example:

        x.dtype ==

    """

    ERROR_CODE = "C2001"



# ============================================================
# AST Exception
# C2002
# ============================================================


class UnsupportedASTNodeError(ConstraintError):
    """
    AST node is not supported.

    Example:

        Import
        Lambda
        ClassDef

    """

    ERROR_CODE = "C2002"



# ============================================================
# Variable Binding Exception
# C2003
# ============================================================


class VariableBindingError(ConstraintError):
    """
    Variable cannot be resolved
    from VariableRegistry.

    Also carries the structured cause (param / attr / cause) so that callers
    can group and render failures without re-parsing the message text.
    """

    ERROR_CODE = "C2003"


    def __init__(
        self,
        message: str,
        *,
        param: Optional[str] = None,
        attr: Optional[str] = None,
        cause: str = "",
        error_code: Optional[str] = None,
    ) -> None:

        super().__init__(
            message,
            error_code=error_code,
        )

        # 出问题的参数名，如 activationInputOptional；无法确定时为 None
        self.param = param

        # 读取失败的属性名，如 dtype / is_present
        self.attr = attr

        # 根因分类：absent / missing_key / missing_attr / unbound
        self.cause = cause



# ============================================================
# Function Registry Exception
# C2004
# ============================================================


class FunctionNotRegisteredError(ConstraintError):
    """
    Function call exists but handler
    is not registered.
    """

    ERROR_CODE = "C2004"

class FunctionAlreadyRegisteredError(
    ConstraintError
):
    """
    Function name already exists.
    """

    ERROR_CODE = "C2005"