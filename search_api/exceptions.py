class UserException(Exception):
    """Raised for invalid client requests (HTTP 400)."""


class SystemException(Exception):
    """Raised for internal service failures (HTTP 503)."""


class ConfigurationException(SystemException):
    """Raised for configuration related internal service failures (HTTP 503)."""
