"""
Coverage Model package.

Public APIs exported here.

External modules should import
from coverage package.

"""


from .pair import Pair

from .universe import PairUniverse

from .coverage import CoverageTracker

from .pair_existence_checker import (
    PairExistenceChecker,
)


from .interfaces import (
    PairProviderProtocol,
    CoverageProtocol,
    PairCheckerProtocol,
)



__all__ = [

    "Pair",

    "PairUniverse",

    "CoverageTracker",

    "PairExistenceChecker",

    "PairProviderProtocol",

    "CoverageProtocol",

    "PairCheckerProtocol",

]