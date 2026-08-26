from .base import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"

    def __init__(self, *, api_key: str, model: str, **kwargs) -> None:
        requested = model.strip()
        super().__init__(
            api_key=api_key,
            model=requested or "openai/gpt-oss-20b",
            endpoint="https://api.groq.com/openai/v1/chat/completions",
            **kwargs,
        )
