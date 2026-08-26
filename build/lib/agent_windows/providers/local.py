from .base import OpenAICompatibleProvider


class LocalLLMProvider(OpenAICompatibleProvider):
    name = "local"

    def __init__(self, *, base_url: str, model: str, **kwargs) -> None:
        endpoint = base_url.rstrip("/") + "/chat/completions"
        super().__init__(api_key="local", model=model, endpoint=endpoint, **kwargs)

    def is_available(self) -> bool:
        return bool(self.model and self.endpoint.startswith(("http://127.0.0.1", "http://localhost")))
