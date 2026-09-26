"""Request errors that carry an HTTP status; the Studio server maps them to responses.

They subclass ValueError, so controllers that already catch ValueError keep working.
"""


class RequestError(ValueError):
    status = 400


class NotFound(RequestError):
    status = 404


class Conflict(RequestError):
    """The resource changed meanwhile, or the action is not available in its current state."""
    status = 409


class Busy(Conflict):
    """Shared capacity (inference, a worker slot) is in use; retry later."""
