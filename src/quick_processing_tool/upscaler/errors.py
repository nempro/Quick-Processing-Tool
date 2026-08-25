class UpscaleError(Exception):
    """Base class for expected upscaler failures."""


class BackendNotFoundError(UpscaleError):
    pass


class ModelNotFoundError(UpscaleError):
    pass


class GPUUnavailableError(UpscaleError):
    pass


class InputDecodeError(UpscaleError):
    pass


class UpscaleProcessingError(UpscaleError):
    pass


class UpscaleSaveError(UpscaleError):
    pass


class UpscaleCancelledError(UpscaleError):
    pass
