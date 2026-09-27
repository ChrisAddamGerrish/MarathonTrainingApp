"""Domain errors raised by the data layer. Handlers turn them into HTTP responses."""


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


class UnauthorizedError(AppError):
    """Not signed in, or the sign-in details were wrong."""

    status_code = 401


class TooManyAttemptsError(AppError):
    status_code = 429


class NotConfiguredError(AppError):
    status_code = 503


class ForbiddenError(AppError):
    """Signed in, but not allowed (not an admin, or the account's setup isn't finished)."""

    status_code = 403
