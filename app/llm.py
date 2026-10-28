"""Thin wrapper around Nebius Token Factory (OpenAI-compatible) for Nemotron models."""
import json
import re
import time
from openai import AsyncOpenAI
from . import config

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.NEBIUS_API_KEY, base_url=config.NEBIUS_BASE_URL, timeout=120, max_retries=2
        )
    return _client


_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str):
    """Nemotron may wrap output in <think> blocks or ```json fences. Pull out the JSON."""
    text = _THINK.sub("", text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append((start, text[start : end + 1]))
    for _, chunk in sorted(candidates):  # whichever bracket opens first wins
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Model did not return valid JSON: {text[:300]}")


class Usage:
    """Per-run counters shown in the UI, so judges can see exactly how each tier was used."""

    def __init__(self):
        self.tiers = {
            "fast": {"model": config.MODEL_FAST, "calls": 0, "tokens": 0, "seconds": 0.0},
            "extract": {"model": config.MODEL_EXTRACT, "calls": 0, "tokens": 0, "seconds": 0.0},
            "reason": {"model": config.MODEL_REASON, "calls": 0, "tokens": 0, "seconds": 0.0},
        }

    def add(self, tier: str, tokens: int, seconds: float):
        t = self.tiers[tier]
        t["calls"] += 1
        t["tokens"] += tokens
        t["seconds"] = round(t["seconds"] + seconds, 2)


MODEL_FOR = {"fast": lambda: config.MODEL_FAST, "extract": lambda: config.MODEL_EXTRACT, "reason": lambda: config.MODEL_REASON}


_NO_THINK_OK = {"fast": True, "extract": True}  # flipped to False if the endpoint rejects the flag


async def _create(model: str, tier: str, messages: list, max_tokens: int):
    """Nano and Super do extraction work, not deep reasoning: turn thinking off so the whole
    token budget goes to the JSON answer (faster, cheaper, no empty replies). Ultra keeps thinking."""
    if _NO_THINK_OK.get(tier):
        try:
            return await client().chat.completions.create(
                model=model, messages=messages, temperature=0.1, max_tokens=max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except Exception as e:
            if "400" not in str(e) and "chat_template" not in str(e):
                raise
            _NO_THINK_OK[tier] = False
    return await client().chat.completions.create(
        model=model, messages=messages, temperature=0.1, max_tokens=max_tokens
    )


async def chat_json(tier: str, system: str, user: str, usage: Usage | None = None,
                    max_tokens: int = 2000, retries: int = 2):
    """Call the model for a tier and return parsed JSON. Retries once with a stricter nudge on bad JSON."""
    model = MODEL_FOR[tier]()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err: Exception | None = None
    for _ in range(retries):
        t0 = time.perf_counter()
        resp = await _create(model, tier, messages, max_tokens)
        if usage is not None:
            tok = resp.usage.total_tokens if resp.usage else 0
            usage.add(tier, tok, time.perf_counter() - t0)
        msg = resp.choices[0].message
        content = msg.content or ""
        if not content.strip():
            # Reasoning models sometimes put everything in a separate reasoning field
            content = getattr(msg, "reasoning_content", None) or (getattr(msg, "model_extra", None) or {}).get("reasoning_content") or ""
        try:
            return extract_json(content)
        except ValueError as e:
            last_err = e
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Return ONLY valid JSON matching the schema. No prose, no markdown."})
    raise last_err  # type: ignore[misc]
