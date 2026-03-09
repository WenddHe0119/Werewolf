import json
import os
import urllib.request
from typing import Dict, List, Optional, Tuple

PROVIDER_ENV = {
    "openai": ("OPENAI_API_KEY", "OPENAI_API_BASE"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE"),
    "google": ("GOOGLE_API_KEY", "GOOGLE_API_BASE"),
    "mistral": ("MISTRAL_API_KEY", "MISTRAL_API_BASE"),
    "cohere": ("COHERE_API_KEY", "COHERE_API_BASE"),
    "ai21": ("AI21_API_KEY", "AI21_API_BASE"),
    "alibaba": ("Ali_API_KEY", "Ali_API_BASE"),
    "ali": ("Ali_API_KEY", "Ali_API_BASE"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE"),
    "meta": ("META_API_KEY", "META_API_BASE"),
    "glm": ("GLM_API_KEY", "GLM_API_BASE"),
    "seeddance": ("SEEDDANCE_API_KEY", "SEEDDANCE_API_BASE"),
    "llama": ("LLAMA_API_KEY", "LLAMA_API_BASE"),
}


def resolve_model_spec(model_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not model_str:
        return None, None
    raw = model_str.strip()
    if raw.lower() in {"rule_based", "none", "null"}:
        return None, None
    if ":" in raw:
        provider, model = raw.split(":", 1)
        return provider.strip().lower(), model.strip()
    return "openai", raw


def provider_env(provider: str) -> Tuple[Optional[str], Optional[str]]:
    keys = PROVIDER_ENV.get(provider)
    if not keys:
        return None, None
    return os.environ.get(keys[0]), os.environ.get(keys[1])


def build_chat_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class OpenAICompatClient:
    def __init__(self, api_key: str, api_base: str):
        self.api_key = api_key
        self.api_base = api_base

    def chat(self, model: str, messages: List[Dict[str, str]], temperature: float, top_p: float, max_tokens: int) -> str:
        url = build_chat_url(self.api_base)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        choices = parsed.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return message.get("content", "")


def build_llm_client(
    model_spec: Optional[str],
    api_key_env: Optional[str] = None,
    api_base_env: Optional[str] = None,
) -> Optional[OpenAICompatClient]:
    if not model_spec:
        return None
    if api_key_env or api_base_env:
        api_key = os.environ.get(api_key_env or "")
        api_base = os.environ.get(api_base_env or "")
    else:
        provider, _ = resolve_model_spec(model_spec)
        if not provider:
            return None
        api_key, api_base = provider_env(provider)
    if not api_key or not api_base:
        return None
    return OpenAICompatClient(api_key, api_base)
