"""Versioned estimates for synchronous Images API edits, never invoice totals.

Verified against https://developers.openai.com/api/docs/pricing on 2026-11-05.
GPT Image 2 / 2.5 cached-input discounts apply to Responses API only.
Dollar rates are per million tokens; amounts are stored as integer microdollars.
"""

from decimal import Decimal, ROUND_HALF_UP

VERSION = "openai-images-standard-2026-11-05"
SOURCE = "https://developers.openai.com/api/docs/pricing"


def rate_card(model):
    cards = {
        "gpt-image-2.6-flare": ("8", "5", "30", None, "2", "1.25"),
        "gpt-image-2.6-sunburst": ("8", "5", "30", None, "2", "1.25"),
        "gpt-image-2.5-flare": ("8", "5", "30", None, "8", "5"),
        "gpt-image-2.5-sunburst": ("8", "5", "30", None, "8", "5"),
        "gpt-image-2": ("8", "5", "30", None, "8", "5"),
        "gpt-image-1.5": ("8", "5", "32", "10", "2", "1.25"),
        "gpt-image-1-mini": ("2.5", "2", "8", None, "0.25", "0.2"),
        "chatgpt-image-latest": ("8", "5", "32", "10", "2", "1.25"),
    }
    values = cards.get(model)
    if values is None:
        return {}
    return dict(
        zip(
            (
                "image_input",
                "text_input",
                "image_output",
                "text_output",
                "cached_image",
                "cached_text",
            ),
            values,
        )
    )


def count(value):
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 10**12
        else None
    )


def estimate(usage, rates):
    """Return nullable counts and cost. Missing breakdowns are never guessed."""
    fields = (
        "input_tokens",
        "image_input_tokens",
        "text_input_tokens",
        "output_tokens",
        "image_output_tokens",
        "text_output_tokens",
    )
    result = dict.fromkeys(fields)
    result.update(cost_microusd=None, meter_status="unreported")
    if not isinstance(usage, dict) or not usage:
        return result
    result["meter_status"] = "partial"
    incoming = usage.get("input_tokens_details", {})
    outgoing = usage.get("output_tokens_details")
    if outgoing is None:
        outgoing = {}
    if not isinstance(incoming, dict) or not isinstance(outgoing, dict):
        return result
    result.update(
        input_tokens=count(usage.get("input_tokens")),
        output_tokens=count(usage.get("output_tokens")),
        image_input_tokens=count(incoming.get("image_tokens")),
        text_input_tokens=count(incoming.get("text_tokens")),
        image_output_tokens=count(outgoing.get("image_tokens")),
        text_output_tokens=count(outgoing.get("text_tokens")),
    )
    if not outgoing and rates and rates.get("text_output") is None:
        result["image_output_tokens"], result["text_output_tokens"] = (
            result["output_tokens"],
            0,
        )
    if not rates or any(result[key] is None for key in fields):
        return result
    if (
        result["input_tokens"]
        != result["image_input_tokens"] + result["text_input_tokens"]
        or result["output_tokens"]
        != result["image_output_tokens"] + result["text_output_tokens"]
    ):
        return result
    if rates.get("text_output") is None and result["text_output_tokens"]:
        return result
    cached = incoming.get("cached_tokens_details", {})
    if not isinstance(cached, dict):
        return result
    cached_image = count(cached.get("image_tokens", 0))
    cached_text = count(cached.get("text_tokens", 0))
    total_cached = count(
        incoming.get(
            "cached_tokens",
            cached_image + cached_text
            if cached_image is not None and cached_text is not None
            else None,
        )
    )
    if (
        cached_image is None
        or cached_text is None
        or total_cached != cached_image + cached_text
    ):
        return result
    if (
        cached_image > result["image_input_tokens"]
        or cached_text > result["text_input_tokens"]
    ):
        return result
    amount = (
        (result["image_input_tokens"] - cached_image) * Decimal(rates["image_input"])
        + cached_image * Decimal(rates["cached_image"])
        + (result["text_input_tokens"] - cached_text) * Decimal(rates["text_input"])
        + cached_text * Decimal(rates["cached_text"])
        + result["image_output_tokens"] * Decimal(rates["image_output"])
        + result["text_output_tokens"] * Decimal(rates.get("text_output") or "0")
    )
    result.update(
        cost_microusd=int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        meter_status="reported",
    )
    return result
