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
    """

    ERROR_CODE = "C2003"



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