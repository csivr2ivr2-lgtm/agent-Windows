class ProviderError(RuntimeError):
    """Base provider failure eligible for fallback."""


class ProviderUnavailable(ProviderError):
    """Provider is not configured or failed its health check."""


class ProviderRateLimited(ProviderError):
    """Provider rejected the request due to a documented quota or rate limit."""

