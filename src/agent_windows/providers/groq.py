from .base import OpenAICompatibleProvider


_DEPRECATED_GROQ_MODELS = {
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "qwen/qwen3-32b": "openai/gpt-oss-120b",
    "meta-llama/llama-4-scout-17b-16e-instruct": "openai/gpt-oss-120b",
    "meta-llama/llama-4-maverick-17b-128e-instruct": "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct-0905": "openai/gpt-oss-120b",
}


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"

    def __init__(self, *, api_key: str, model: str, **kwargs) -> None:
        selected_model = _DEPRECATED_GROQ_MODELS.get(model.strip(), model.strip() or "openai/gpt-oss-20b")
        super().__init__(
            api_key=api_key,
            model=selected_model,
            endpoint="https://api.groq.com/openai/v1/chat/completions",
            **kwargs,
        )
