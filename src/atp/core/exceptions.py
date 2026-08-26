"""
ATP Exception Classes

Custom exceptions for ATP operations.
"""


class ATPError(Exception):
    """Base exception for all ATP-related errors."""

    pass


class ATPConfigError(ATPError):
    """Raised when ATP configuration is invalid or missing."""

    pass


class ATPRegistrationError(ATPError):
    """Raised when system registration fails."""

    pass


class ATPCommitError(ATPError):
    """Error during commit creation."""

    pass


class ATPQueryError(ATPError):
    """Error during commit or system query."""

    pass


class ATPVerificationError(ATPError):
    """Error during commit verification."""

    pass


class ATPNetworkError(ATPError):
    """Raised when network communication with backend fails."""

    pass


class ATPAuthError(ATPError):
    """Raised when API key authentication fails."""

    pass


class ATPStorageError(ATPError):
    """Base exception for storage-related errors."""

    pass


class ATPProofNotFoundError(ATPStorageError):
    """Raised when proof is not found in storage."""

    pass


class ATPProofExpiredError(ATPStorageError):
    """Raised when proof has expired past its TTL."""

    pass
