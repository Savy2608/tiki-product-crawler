"""Provider-agnostic LLM wrapper: cache, validation retry, token and cost tracking.

The rest of the pipeline only talks to LLMClient. A provider only has to turn
(system prompt, user text, JSON schema) into (text, stop reason, input tokens,
output tokens). The Anthropic SDK is imported lazily, so the unit tests (which
use a fake provider) run without it.
"""
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from enrichment.schemas import validate

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


class CostLimitReached(Exception):
    """The run spent LLM_MAX_COST_USD; progress is saved and the run stops."""


class FatalLLMError(Exception):
    """An error that retrying will not fix (bad key, bad request): stop the run."""


class TransientLLMError(Exception):
    """A temporary error left after the SDK's own retries: try the product next run."""


@dataclass
class Prompt:
    version: str
    text: str

    @property
    def sha(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]


def load_prompt(version, **values):
    """Read prompts/<version>.md and fill {placeholders} given as keyword arguments."""
    with open(os.path.join(PROMPTS_DIR, version + ".md"), encoding="utf-8") as f:
        text = f.read().strip()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return Prompt(version, text)


@dataclass
class LLMResult:
    data: Optional[dict]   # validated output, or None when rejected
    error: Optional[str]   # why the output was rejected
    raw: str               # last raw answer, kept for the rejects file
    usage: dict = field(default_factory=dict)


class CostTracker:
    """Thread-safe running total of the spend, with a hard limit."""

    def __init__(self, limit_usd, already_spent=0.0):
        self.limit = limit_usd
        self.spent = already_spent
        self._lock = threading.Lock()

    def check(self):
        with self._lock:
            if self.spent >= self.limit:
                raise CostLimitReached(f"spend {self.spent:.4f} USD reached the "
                                       f"{self.limit:.2f} USD limit")

    def add(self, usd):
        with self._lock:
            self.spent += usd


class AnthropicProvider:
    """Claude through the Anthropic Messages API with structured outputs."""

    name = "anthropic"

    def __init__(self, api_key, model, timeout, max_retries=4):
        import anthropic  # needs Python 3.10+, only when the real API is used
        if not api_key:
            raise SystemExit("LLM_API_KEY is not set: add it to .env (see .env.example)")
        self._anthropic = anthropic
        self.model = model
        # Ask for gzip only: an old brotlicffi package (for example in Anaconda)
        # breaks the SDK's brotli decoder with a TypeError on every response.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout,
                                          max_retries=max_retries,
                                          default_headers={"Accept-Encoding": "gzip, deflate"})

    def complete(self, system, user, json_schema, max_tokens):
        a = self._anthropic
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                # Low effort is enough for short extraction; sampling parameters
                # such as temperature are not accepted by this model.
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": json_schema}},
            )
        except (a.AuthenticationError, a.PermissionDeniedError, a.BadRequestError,
                a.NotFoundError) as exc:
            raise FatalLLMError(f"{type(exc).__name__}: {exc.message}") from None
        except (a.RateLimitError, a.InternalServerError, a.APIConnectionError,
                a.APIStatusError) as exc:
            raise TransientLLMError(f"{type(exc).__name__}: {exc}") from None
        text = "".join(block.text for block in response.content if block.type == "text")
        return text, response.stop_reason, response.usage.input_tokens, response.usage.output_tokens


class LLMClient:
    """Calls a provider with caching, one validation retry and cost tracking."""

    def __init__(self, provider, model, prices, cache_dir, tracker, max_tokens=2048):
        self.provider = provider
        self.model = model
        self.price_in, self.price_out = prices
        self.cache_dir = cache_dir
        self.tracker = tracker
        self.max_tokens = max_tokens
        os.makedirs(cache_dir, exist_ok=True)

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000

    def _cache_path(self, prompt, user_text):
        key = json.dumps([self.model, prompt.version, prompt.sha, user_text], ensure_ascii=False)
        return os.path.join(self.cache_dir, hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def call(self, prompt, user_text, model_cls, json_schema):
        path = self._cache_path(prompt, user_text)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            data, error = validate(model_cls, cached["raw"])
            if data is not None:  # identical request: no API call, nothing spent now
                usage = dict(cached["usage"], cached=True, api_cost_usd=0.0)
                return LLMResult(data, None, cached["raw"], usage)

        # cost_usd: what this answer cost when it was produced (kept on cache hits);
        # api_cost_usd: what this call spent now (0 on cache hits).
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "api_cost_usd": 0.0,
                 "latency_ms": 0, "cached": False, "attempts": 0}
        message, raw, error = user_text, "", None
        for _ in range(2):  # first answer, then one retry with the validation error
            self.tracker.check()
            start = time.time()
            raw, stop_reason, tokens_in, tokens_out = self.provider.complete(
                prompt.text, message, json_schema, self.max_tokens)
            spent = self.cost(tokens_in, tokens_out)
            self.tracker.add(spent)
            usage["input_tokens"] += tokens_in
            usage["output_tokens"] += tokens_out
            usage["cost_usd"] += spent
            usage["api_cost_usd"] += spent
            usage["latency_ms"] += int((time.time() - start) * 1000)
            usage["attempts"] += 1

            if stop_reason == "refusal":
                error = "the model refused to answer"
                continue
            if stop_reason == "max_tokens":
                error = f"answer cut off at max_tokens={self.max_tokens}"
                continue
            data, error = validate(model_cls, raw)
            if data is not None:
                self._save_cache(path, raw, usage)
                return LLMResult(data, None, raw, usage)
            message = (f"{user_text}\n\nYour previous answer was not valid ({error}). "
                       "Answer again with JSON that matches the schema exactly.")
        return LLMResult(None, error, raw, usage)

    @staticmethod
    def _save_cache(path, raw, usage):
        tmp_path = f"{path}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"raw": raw, "usage": usage}, f, ensure_ascii=False)
        os.replace(tmp_path, path)


def make_provider(config):
    if config.llm_provider == "anthropic":
        return AnthropicProvider(config.llm_api_key, config.llm_model, config.timeout)
    raise SystemExit(f"Unknown LLM_PROVIDER {config.llm_provider!r} (supported: anthropic)")
