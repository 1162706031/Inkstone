"""Business errors raised by the service layer.

Services stay independent of FastAPI; the API layer maps ``status`` to the HTTP response.
"""


class ServiceError(Exception):
    status = 400

    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail)


class Unauthenticated(ServiceError):
    status = 401


class Forbidden(ServiceError):
    status = 403


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class Gone(ServiceError):
    status = 410


class InvalidInput(ServiceError):
    status = 422


class RateLimited(ServiceError):
    status = 429


class UpstreamFailed(ServiceError):
    status = 502


class Unavailable(ServiceError):
    status = 503
