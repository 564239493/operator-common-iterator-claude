
"""
Model Layer Exception Definition.

Architecture Version:
    V1.2

Module:
    M1 Model Layer

Description:
    Centralized exception hierarchy.

Design Rules:
    1. All business exceptions inherit ModelError.
    2. Error code is immutable by subclass definition.
    3. Runtime error_code override is supported.
    4. Exception message format:
           [ERROR_CODE] message
    1. 提供统一异常体系
    2. 提供错误码
    3. 支持日志定位
    4. 支持未来API层直接映射错误响应
    5. 支持单元测试精确断言

"""

from typing import Optional


# ============================================================
# Base Exception
# ============================================================


class ModelError(Exception):
    """
    Base exception of Model Layer.
    """

    ERROR_CODE = "M1000"

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
# Parameter Model Exception
# Error Range:
#     M1200 - M1209
# ============================================================


class ParameterModelError(ModelError):
    """
    Base exception of ParameterModel.
    """

    ERROR_CODE = "M1200"



class InvalidParameterError(ParameterModelError):
    """
    Invalid parameter definition.

    Examples:
        - invalid parameter structure
        - inconsistent attribute definition
    """

    ERROR_CODE = "M1201"



class EmptyParameterNameError(ParameterModelError):
    """
    Parameter name is empty.
    """

    ERROR_CODE = "M1202"



class EmptyAttributeDomainError(ParameterModelError):
    """
    Parameter attribute domain is empty.

    Example:

        dtype: []

    """

    ERROR_CODE = "M1203"



class ParameterAttributeError(ParameterModelError):
    """
    Invalid or unsupported parameter attribute.
    """

    ERROR_CODE = "M1204"



# ============================================================
# Variable Model / Variable Registry Exception
# Error Range:
#     M1210 - M1219
# ============================================================


class VariableModelError(ModelError):
    """
    Base exception of VariableModel.
    """

    ERROR_CODE = "M1210"



class InvalidVariableError(VariableModelError):
    """
    Invalid variable definition.
    """

    ERROR_CODE = "M1211"



class DuplicateVariableError(VariableModelError):
    """
    Variable already registered.
    """

    ERROR_CODE = "M1212"



class VariableNotFoundError(VariableModelError):
    """
    Variable does not exist.
    """

    ERROR_CODE = "M1213"



class InvalidVariableIdError(VariableModelError):
    """
    Invalid variable ID.
    """

    ERROR_CODE = "M1214"



# ============================================================
# Value Registry Exception
# Error Range:
#     M1300 - M1399
# ============================================================


class ValueRegistryError(ModelError):
    """
    Base exception of ValueRegistry.
    """

    ERROR_CODE = "M1300"



class InvalidValueError(ValueRegistryError):
    """
    Invalid value definition.
    """

    ERROR_CODE = "M1301"



class ValueIdNotFoundError(ValueRegistryError):
    """
    Value ID does not exist.
    """

    ERROR_CODE = "M1302"



class DuplicateValueError(ValueRegistryError):
    """
    Value already exists.
    """

    ERROR_CODE = "M1303"



# ============================================================
# Constraint Context Exception
# Error Range:
#     M1400 - M1499
# ============================================================


class ContextError(ModelError):
    """
    Base exception of ConstraintContext.
    """

    ERROR_CODE = "M1400"



class ContextVariableMissingError(ContextError):
    """
    Variable does not exist in context.
    """

    ERROR_CODE = "M1401"



class ContextValueMissingError(ContextError):
    """
    Variable exists but value is missing.
    """

    ERROR_CODE = "M1402"



class ContextVariableConflictError(ContextError):
    """
    Variable assignment conflict.
    """

    ERROR_CODE = "M1403"



class ContextFrozenError(ContextError):
    """
    Context is frozen and cannot be modified.
    """

    ERROR_CODE = "M1404"





