"""
Coverage Parameter Model.

M3-Step1

"""

from dataclasses import dataclass
from typing import Dict


@dataclass(
    frozen=True
)
class Parameter:
    """
    Test parameter.

    Example:

        x

    """

    name: str

    attributes: Dict[str, list]

    def __post_init__(self):

        if not self.name:
            raise ValueError(
                "Parameter name cannot be empty"
            )

        if self.attributes is None:
            raise ValueError(
                "attributes cannot be None"
            )


@dataclass(
    frozen=True
)
class Factor:
    """
    Coverage factor.

    Example:

        x.dtype

    """

    parameter: str

    attribute: str

    @property
    def name(
            self
    ) -> str:
        """
        Full factor name.

        Example:

            x.dtype

        """

        return (
            f"{self.parameter}."
            f"{self.attribute}"
        )

    def __post_init__(self):

        if not self.parameter:
            raise ValueError(
                "parameter cannot be empty"
            )

        if not self.attribute:
            raise ValueError(
                "attribute cannot be empty"
            )
