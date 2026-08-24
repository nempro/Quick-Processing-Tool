class ProcessingError(Exception):
    """A user-facing processing failure."""


class UnsupportedImageError(ProcessingError):
    """The input cannot be opened as a supported image."""


class TargetSizeUnreachable(ProcessingError):
    """The requested byte size cannot be met safely."""
