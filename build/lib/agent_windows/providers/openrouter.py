from .base import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, *, api_key: str, model: str, app_name: str = "agent-Windows", **kwargs) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            extra_headers={"X-Title": app_name},
            **kwargs,
        )
