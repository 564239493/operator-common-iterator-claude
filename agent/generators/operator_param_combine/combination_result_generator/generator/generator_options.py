from dataclasses import dataclass
from typing import Optional


@dataclass
class GeneratorOptions:
    """
    Generator configuration.
    """

    strength: int = 2
    target_coverage: float = 1.0
    max_iterations: int = 10000
    random_seed: Optional[int] = None
