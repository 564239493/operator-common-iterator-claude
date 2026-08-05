class GeneratorError(Exception):
    """
    Base generator exception.
    """

    ERROR_CODE = "G4000"

    def __init__(self, message: str, *, error_code: str | None = None, ) -> None:
        self.message = message
        self.error_code = error_code or self.ERROR_CODE
        super().__init__(f"[{self.error_code}] {self.message}")


class InvalidGeneratorConfigError(
    GeneratorError
):
    ERROR_CODE = "G4001"


class GenerationFailedError(
    GeneratorError
):
    ERROR_CODE = "G4002"


class CoverageNotReachedError(
    GeneratorError
):
    ERROR_CODE = "G4003"

class CandidateGenerationError(
    GeneratorError
):

    ERROR_CODE = "G4004"
