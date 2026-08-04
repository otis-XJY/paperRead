"""Shared LLM client used by the paper workflow and the daily report.

This module intentionally does not import ``main``, Zotero, ActivityWatch,
or Feishu workflow code.  Utilities can therefore reuse the same model pool
without triggering unrelated integrations during module import.
"""

import os
import time
from dataclasses import dataclass
from types import SimpleNamespace

from openai import OpenAI


DEFAULT_FALLBACK_MODELS = [
    "tencent/hy3",
    "inclusionAI/Ring-2.6-1T",
    "MiniMax/MiniMax-M1-80k",
    "ZhipuAI/GLM-5.1",
    "meituan-longcat/LongCat-Flash-Lite",
    "Qwen/Qwen3-8B",
    "moonshotai/Kimi-K2.5",
    "MiniMax/MiniMax-M2.7",
    "XiaomiMiMo/MiMo-V2-Flash:xiaomi",
    "stepfun-ai/Step-3.7-Flash",
    "deepseek-ai/DeepSeek-V3.2",
    "ZhipuAI/GLM-5",
    "deepseek-ai/DeepSeek-V4-Flash",
]

# Model selection belongs in source control, not in GitHub configuration.
# GitHub Actions only supplies the matching API keys through Secrets.
DEFAULT_ZHIPU_MODELS = [
    # Free text models, ordered from lower to higher general capability.
    "glm-4-flash-250414",
    "glm-4.7-flash",
    "glm-4.7",
    "glm-5",
    "glm-5.2",
]

DEFAULT_OPENAI_MODELS = [
    "gpt-4o-mini",
]

# Add an OpenAI-compatible provider here when it is needed.  Keep its API key
# in the GitHub Secret named by ``api_key_env``; never put a token in this file.
# Example:
# {
#     "provider": "example",
#     "api_key_env": "LLM_PROVIDER_1_API_KEY",
#     "base_url": "https://example.com/v1/",
#     "models": ["example-model"],
#     "stream": False,
#     "supports_response_format": False,
# },
DEFAULT_EXTRA_PROVIDERS = []


@dataclass(frozen=True)
class LLMEndpoint:
    """One model exposed by one API provider."""

    provider: str
    model: str
    client: object
    stream: bool = False
    supports_response_format: bool = False

    @property
    def key(self):
        return f"{self.provider}:{self.model}"


def _normalize_model_id(model, base_url):
    """Remove provider suffixes when calling ModelScope model IDs."""
    model = str(model or "").strip()
    if "modelscope.cn" in str(base_url).lower() and ":" in model:
        return model.split(":", 1)[0]
    return model


def get_model_pool(client=None, base_url=None):
    """Return configured models that the current API provider supports."""
    configured_model = os.getenv("LLM_MODEL")
    candidates = list(dict.fromkeys(
        ([_normalize_model_id(configured_model, base_url)] if configured_model else [])
        + [_normalize_model_id(model, base_url) for model in DEFAULT_FALLBACK_MODELS]
    ))
    if client is None or "modelscope.cn" not in str(base_url or "").lower():
        return candidates

    try:
        available = {
            str(item.id)
            for item in client.models.list().data
            if getattr(item, "id", None)
        }
        supported = [model for model in candidates if model in available]
        if supported:
            return supported
        print("ModelScope 当前可用模型中没有匹配默认候选，将继续尝试配置列表。")
    except Exception as exc:
        print(f"无法读取 ModelScope 可用模型列表，将使用配置候选: {exc}")
    return candidates


def is_auth_error(exc):
    """Return whether an exception looks like an authentication failure."""
    msg = str(exc).lower()
    return (
        "401" in msg
        or "authentication failed" in msg
        or "invalid api key" in msg
        or "invalid token" in msg
        or "unauthorized" in msg
    )


def is_rate_limit_error(exc):
    """Return whether an exception looks like a rate-limit failure."""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def is_daily_quota_error(exc):
    """Return whether a model cannot be retried until the next quota window."""
    msg = str(exc).lower()
    return "exceeded today's quota" in msg or "try again tomorrow" in msg


def is_unavailable_model_error(exc):
    """Return whether the current endpoint cannot serve a model ID."""
    msg = str(exc).lower()
    return (
        "unsupported model" in msg
        or "model is unavailable for free" in msg
        or "use this slug instead" in msg
    )


def is_modelscope_client(client):
    """Return whether an OpenAI-compatible client targets ModelScope."""
    return "modelscope.cn" in str(getattr(client, "base_url", "")).lower()


def collect_streamed_content(stream):
    """Collect final assistant text from a ModelScope streaming response."""
    content_parts = []
    for chunk in stream:
        for choice in getattr(chunk, "choices", None) or []:
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if content:
                content_parts.append(content)

    content = "".join(content_parts).strip()
    if not content:
        raise ValueError("ModelScope returned no assistant content in its stream")
    return content


def create_openai_endpoints(
    provider,
    api_key,
    base_url,
    models,
    *,
    stream=False,
    supports_response_format=False,
):
    """Create OpenAI-compatible endpoints for one provider without logging keys."""
    if not api_key:
        return []
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )
    return [
        LLMEndpoint(
            provider=provider,
            model=model,
            client=client,
            stream=stream,
            supports_response_format=supports_response_format,
        )
        for model in models
    ]


def build_llm_endpoints():
    """Build the ordered cross-provider pool using code-defined model lists."""
    endpoints = []

    modelscope_key = os.getenv("MODELSCOPE_API_KEY")
    if modelscope_key:
        modelscope_base_url = (
            os.getenv("MODELSCOPE_BASE_URL")
            or os.getenv("BASE_URL")  # Backward-compatible ModelScope setting.
            or "https://api-inference.modelscope.cn/v1/"
        )
        modelscope_client = OpenAI(
            api_key=modelscope_key,
            base_url=modelscope_base_url,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        )
        endpoints.extend(create_openai_endpoints(
            "modelscope",
            modelscope_key,
            modelscope_base_url,
            get_model_pool(modelscope_client, modelscope_base_url),
            stream=True,
        ))

    zhipu_key = os.getenv("ZHIPUAI_API_KEY") or os.getenv("ZAI_API_KEY")
    if zhipu_key:
        endpoints.extend(create_openai_endpoints(
            "zhipu",
            zhipu_key,
            "https://open.bigmodel.cn/api/paas/v4/",
            DEFAULT_ZHIPU_MODELS,
        ))

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        endpoints.extend(create_openai_endpoints(
            "openai",
            openai_key,
            "https://api.openai.com/v1/",
            DEFAULT_OPENAI_MODELS,
            supports_response_format=True,
        ))

    for config in DEFAULT_EXTRA_PROVIDERS:
        api_key_env = config["api_key_env"]
        api_key = os.getenv(api_key_env)
        if not api_key:
            print(f"LLM provider skipped because {api_key_env} is not set: {config['provider']}")
            continue
        endpoints.extend(create_openai_endpoints(
            config["provider"],
            api_key,
            config["base_url"],
            config["models"],
            stream=config.get("stream", False),
            supports_response_format=config.get("supports_response_format", False),
        ))
    return endpoints


class MultiModelLLM:
    """Call models from multiple OpenAI-compatible providers in round-robin order."""

    def __init__(self, client=None, models=None, endpoints=None):
        if endpoints is None:
            # Backward-compatible constructor for code that supplies one client.
            endpoints = [
                LLMEndpoint("default", model, client)
                for model in list(dict.fromkeys(models or []))
            ]
        self.endpoints = list(endpoints)
        if not self.endpoints:
            raise ValueError("LLM endpoint pool is empty")
        self.current_idx = 0
        self.next_start_idx = 0
        self._model_failures = {endpoint.key: 0 for endpoint in self.endpoints}
        self._disabled_endpoints = set()

    @property
    def current_model(self):
        return self.endpoints[self.current_idx].model

    def call(self, messages, response_format=None, max_rounds=2, response_validator=None):
        max_rounds = max(int(os.getenv("LLM_MAX_ROUNDS", str(max_rounds))), 1)
        total_endpoints = len(self.endpoints)
        last_exc = None
        start_idx = self.next_start_idx % total_endpoints

        for round_idx in range(max_rounds):
            if round_idx > 0:
                if len(self._disabled_endpoints) == total_endpoints:
                    break
                print(
                    "All LLM endpoints failed in previous round; "
                    f"waiting 60s before round {round_idx + 1}..."
                )
                time.sleep(60)

            for offset in range(total_endpoints):
                idx = (start_idx + offset) % total_endpoints
                endpoint = self.endpoints[idx]
                if endpoint.key in self._disabled_endpoints:
                    continue
                attempt_started = time.monotonic()
                print(
                    f"LLM attempt started: provider={endpoint.provider} "
                    f"model={endpoint.model}",
                    flush=True,
                )
                try:
                    kwargs = {"model": endpoint.model, "messages": messages}
                    if endpoint.stream:
                        kwargs["stream"] = True
                        if (
                            endpoint.provider == "modelscope"
                            and endpoint.model == "Qwen/Qwen3-8B"
                        ):
                            kwargs["extra_body"] = {"enable_thinking": True}
                        content = collect_streamed_content(
                            endpoint.client.chat.completions.create(**kwargs)
                        )
                        response = SimpleNamespace(
                            choices=[SimpleNamespace(
                                message=SimpleNamespace(content=content)
                            )]
                        )
                    else:
                        if response_format and endpoint.supports_response_format:
                            kwargs["response_format"] = response_format
                        response = endpoint.client.chat.completions.create(**kwargs)
                        content = response.choices[0].message.content if response.choices else None
                    if content is None:
                        raise ValueError(
                            f"Model {endpoint.model} returned empty response "
                            f"(choices={response.choices})"
                        )
                    if response_validator:
                        response_validator(response)

                    self.current_idx = idx
                    self.next_start_idx = (idx + 1) % total_endpoints
                    print(
                        f"LLM attempt succeeded: provider={endpoint.provider} "
                        f"model={endpoint.model} "
                        f"elapsed_seconds={time.monotonic() - attempt_started:.1f}",
                        flush=True,
                    )
                    return response
                except Exception as exc:
                    last_exc = exc
                    self._model_failures[endpoint.key] = (
                        self._model_failures.get(endpoint.key, 0) + 1
                    )
                    if is_daily_quota_error(exc) or is_unavailable_model_error(exc):
                        self._disabled_endpoints.add(endpoint.key)
                    if is_rate_limit_error(exc):
                        reason = "rate limited"
                    elif is_auth_error(exc):
                        reason = "auth failed"
                    else:
                        reason = "call failed"
                    print(
                        f"LLM endpoint failed, trying next: provider={endpoint.provider} "
                        f"model={endpoint.model} ({reason}: {exc}); "
                        f"elapsed_seconds={time.monotonic() - attempt_started:.1f}",
                        flush=True,
                    )

            if len(self._disabled_endpoints) == total_endpoints:
                break

        raise RuntimeError(
            f"All LLM endpoints failed after {max_rounds} rounds. Last error: {last_exc}"
        )


MODEL_ENDPOINTS = build_llm_endpoints()
if not MODEL_ENDPOINTS:
    raise ValueError(
        "Missing LLM API key; set MODELSCOPE_API_KEY, ZHIPUAI_API_KEY, "
        "OPENAI_API_KEY, or configure an extra provider."
    )

# Kept as a simple model-name list for existing diagnostics/importers.
MODEL_POOL = [endpoint.model for endpoint in MODEL_ENDPOINTS]
llm = MultiModelLLM(endpoints=MODEL_ENDPOINTS)
print(
    "LLM endpoint pool (equal priority): "
    + str([f"{endpoint.provider}/{endpoint.model}" for endpoint in MODEL_ENDPOINTS])
)
