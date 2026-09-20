"""Domain errors raised by the data layer. main.py turns them into HTTP responses."""


class AppError(Exception):
    status_code = 500

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    """The request is valid but clashes with the current state of the data."""

    status_code = 409


class UnprocessableError(AppError):
    status_code = 422
