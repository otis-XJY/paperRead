"""Shared LLM client used by the paper workflow and the daily report.

This module intentionally does not import ``main``, Zotero, ActivityWatch,
or Feishu workflow code.  Utilities can therefore reuse the same model pool
without triggering unrelated integrations during module import.
"""

import os
import time

from openai import OpenAI


DEFAULT_FALLBACK_MODELS = [
    "Tencent-Hunyuan/Hy3",
    "ZhipuAI/GLM-5.1",
    "Qwen/Qwen3-8B",
    "moonshotai/Kimi-K2.5",
    "MiniMax/MiniMax-M2.7",
    "XiaomiMiMo/MiMo-V2-Flash:xiaomi",
    "stepfun-ai/Step-3.7-Flash",
    "deepseek-ai/DeepSeek-V3.2",
    "ZhipuAI/GLM-5",
    "deepseek-ai/DeepSeek-V4-Flash",
]


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


class MultiModelLLM:
    """Call equally-prioritized models with round-robin fallback."""

    def __init__(self, client, models):
        self.client = client
        self.models = list(dict.fromkeys(models))
        if not self.models:
            raise ValueError("LLM model pool is empty")
        self.current_idx = 0
        self.next_start_idx = 0
        self._model_failures = {model: 0 for model in self.models}

    @property
    def current_model(self):
        return self.models[self.current_idx]

    def call(self, messages, response_format=None, max_rounds=2, response_validator=None):
        max_rounds = max(int(os.getenv("LLM_MAX_ROUNDS", str(max_rounds))), 1)
        total_models = len(self.models)
        last_exc = None
        start_idx = self.next_start_idx % total_models

        for round_idx in range(max_rounds):
            if round_idx > 0:
                print(
                    "All LLM models failed in previous round; "
                    f"waiting 60s before round {round_idx + 1}..."
                )
                time.sleep(60)

            for offset in range(total_models):
                idx = (start_idx + offset) % total_models
                model = self.models[idx]
                attempt_started = time.monotonic()
                print(f"LLM attempt started: model={model}", flush=True)
                try:
                    kwargs = {"model": model, "messages": messages}
                    if response_format:
                        kwargs["response_format"] = response_format

                    response = self.client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content if response.choices else None
                    if content is None:
                        raise ValueError(
                            f"Model {model} returned empty response "
                            f"(choices={response.choices})"
                        )
                    if response_validator:
                        response_validator(response)

                    self.current_idx = idx
                    self.next_start_idx = (idx + 1) % total_models
                    print(
                        f"LLM attempt succeeded: model={model} elapsed_seconds={time.monotonic() - attempt_started:.1f}",
                        flush=True,
                    )
                    return response
                except Exception as exc:
                    last_exc = exc
                    self._model_failures[model] = self._model_failures.get(model, 0) + 1
                    if is_rate_limit_error(exc):
                        reason = "rate limited"
                    elif is_auth_error(exc):
                        reason = "auth failed"
                    else:
                        reason = "call failed"
                    print(
                        f"LLM model failed, trying next: {model} ({reason}: {exc}); "
                        f"elapsed_seconds={time.monotonic() - attempt_started:.1f}",
                        flush=True,
                    )

        raise RuntimeError(
            f"All LLM models failed after {max_rounds} rounds. Last error: {last_exc}"
        )


LLM_API_KEY = os.getenv("MODELSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
if not LLM_API_KEY:
    raise ValueError(
        "Missing LLM API key; set MODELSCOPE_API_KEY or OPENAI_API_KEY"
    )

BASE_URL = os.getenv("BASE_URL") or "https://api-inference.modelscope.cn/v1/"
client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=BASE_URL,
    timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
    max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
)
MODEL_POOL = get_model_pool(client, BASE_URL)
llm = MultiModelLLM(client, MODEL_POOL)
print(f"LLM model pool (equal priority): {MODEL_POOL}")
