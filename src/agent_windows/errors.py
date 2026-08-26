class ProviderError(RuntimeError):
    """Base provider failure eligible for fallback."""


class ProviderUnavailable(ProviderError):
    """Provider is not configured or failed its health check."""


class ProviderRateLimited(ProviderError):
    """Provider rejected the request due to a documented quota or rate limit."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthenticationError(ProviderError):
    """Credentials are absent or invalid (normally HTTP 401)."""


class ProviderPermissionError(ProviderAuthenticationError):
    """Credentials were accepted but access/model permission was denied (HTTP 403)."""


class ProviderTimeout(ProviderError):
    """The request exceeded its configured deadline."""


class ProviderConnectionError(ProviderError):
    """The provider could not be reached."""


class ProviderServerError(ProviderError):
    """The provider returned HTTP 5xx."""


class ProviderBadResponse(ProviderError):
    """The provider returned invalid or unsupported data."""
