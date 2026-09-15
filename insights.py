# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Local LLM client and insight generation.
# =============================================================================

"""Talking to a local Ollama instance and degrading gracefully when it fails.

The dashboard must remain usable when the model is unavailable, so every
failure path ends in a deterministic summary computed from the same metrics
that would have been sent to the model. Failures are classified so the user
sees an actionable message instead of a generic error.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import requests

from config import Config as config

logger = logging.getLogger(__name__)

# Substrings that identify an out-of-memory condition in an Ollama error body.
# Ollama does not expose a machine-readable error code for this, so matching on
# the message is the only option available at the API level.
_OOM_MARKERS = (
    "out of memory",
    "insufficient memory",
    "cudamalloc",
    "failed to allocate",
    "no available devices",
)


class InsightStatus(str, Enum):
    """Outcome of an insight request, used to drive the status label in the UI."""

    OK = "OK"
    NO_DATA = "No data"
    MODEL_BUSY = "Model busy"
    UNAVAILABLE = "Ollama unavailable"
    TIMEOUT = "Timeout"
    EMPTY = "Empty response"


@dataclass(frozen=True)
class Insight:
    """An insight plus the status that produced it."""

    status: InsightStatus
    text: str


class OllamaError(RuntimeError):
    """Base class for Ollama call failures."""


class OllamaUnavailable(OllamaError):
    """The server could not be reached at all."""


class OllamaTimeout(OllamaError):
    """The server accepted the request but did not answer in time."""


class OllamaOutOfMemory(OllamaError):
    """The server could not load the model, typically because the GPU is full."""


def build_prompt(metrics: dict[str, Any], currency: str) -> str:
    """Build the instruction sent to the model, grounded only in the metrics."""
    return (
        "You are a personal financial analyst. Write a short, objective commentary "
        "(8-12 lines) based ONLY on the aggregated metrics below.\n\n"
        "Rules:\n"
        "- Never invent numbers; use only the values provided.\n"
        f"- All amounts are expressed in {currency}.\n"
        "- Negative trends mean spending decreased; positive means it increased.\n"
        "- A metric that is null or missing was not computable from the data; "
        "skip it silently rather than commenting on its absence or guessing a value.\n"
        "- Cover: net balance; both the spending AND income trends, and what their "
        "difference implies about the change in net balance; largest categories; "
        "volatility; the split between credit and debit spending, when present; and "
        "upcoming installment commitments (`upcoming_installments`) — call out "
        "purchases finishing soon as freed-up monthly budget, and purchases "
        "starting soon as new recurring commitment.\n"
        "- Finish with exactly three numbered, practical actions. Prefer actions "
        "grounded in `upcoming_installments` or `payment_type_share` when they are "
        "present and material, over generic advice restating the totals.\n\n"
        f"METRICS (JSON):\n{json.dumps(metrics, ensure_ascii=False)}\n"
    )


def _classify_http_error(response: requests.Response) -> OllamaError:
    """Map an HTTP error response onto a specific exception type."""
    body = ""
    try:
        body = response.text[:500].lower()
    except Exception:  # pragma: no cover - defensive, body is best-effort
        pass

    if any(marker in body for marker in _OOM_MARKERS):
        return OllamaOutOfMemory(
            "The model could not be loaded because the GPU or system memory is full."
        )
    if response.status_code == 404:
        return OllamaError(
            f"Model {config.ollama_model!r} is not available on the Ollama server. "
            f"Pull it first."
        )
    return OllamaError(f"Ollama returned HTTP {response.status_code}.")


def generate(prompt: str, model: str | None = None) -> str:
    """Call Ollama's generate endpoint, retrying only on timeouts.

    Connection errors are not retried: if the server is down, a second attempt
    a second later will fail the same way and only doubles the user's wait.
    """
    model = model or config.ollama_model
    url = f"{config.ollama_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9},
    }

    attempts = max(1, config.ollama_retries + 1)
    last_error: OllamaError | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=config.ollama_timeout)
            if response.status_code >= 400:
                raise _classify_http_error(response)
            return (response.json().get("response") or "").strip()

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as exc:
            last_error = OllamaTimeout(
                f"No response within {config.ollama_timeout}s. The model may still be loading."
            )
            logger.warning("Ollama timeout (attempt %d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(1.0)

        except requests.exceptions.ConnectionError as exc:
            logger.error("Cannot reach Ollama at %s: %s", config.ollama_url, exc)
            raise OllamaUnavailable(
                f"Cannot reach the Ollama server at {config.ollama_url}."
            ) from exc

        except ValueError as exc:  # malformed JSON body
            logger.error("Malformed response from Ollama: %s", exc)
            raise OllamaError("The Ollama server returned an unreadable response.") from exc

    assert last_error is not None
    raise last_error


def _fallback_summary(metrics: dict[str, Any], reason: str) -> str:
    """Deterministic summary shown whenever the model cannot be used."""
    totals = metrics["totals"]
    window = metrics["window"]
    monthly = metrics["monthly"]

    lines = [
        "**Summary generated without AI**",
        "",
        f"- Period: {window['start']} to {window['end']}",
        f"- Income: {totals['income']:.2f}",
        f"- Expenses: {totals['expense']:.2f}",
        f"- Net balance: {totals['net']:.2f}",
        f"- Average monthly expense: {monthly['expense_mean']:.2f}",
    ]

    trend = monthly.get("expense_trend_3m")
    if trend is not None:
        direction = "up" if trend > 0 else "down"
        lines.append(f"- 3-month expense trend: {direction} {abs(trend) * 100:.1f}%")

    income_trend = monthly.get("income_trend_3m")
    if income_trend is not None:
        direction = "up" if income_trend > 0 else "down"
        lines.append(f"- 3-month income trend: {direction} {abs(income_trend) * 100:.1f}%")

    finishing = metrics.get("upcoming_installments", {}).get("finishing_soon", {})
    if finishing.get("count"):
        months = metrics["upcoming_installments"]["horizon_months"]
        lines.append(
            f"- {finishing['count']} installment purchase(s) finish within {months} "
            f"month(s), freeing up {finishing['value']:.2f}/month"
        )

    top_categories = metrics.get("top", {}).get("categories", {})
    if top_categories:
        top_name, top_value = next(iter(top_categories.items()))
        lines.append(f"- Largest category: {top_name} ({top_value:.2f})")

    lines += ["", f"_{reason}_"]
    return "\n".join(lines)


def get_insight(metrics: dict[str, Any], model: str | None = None) -> Insight:
    """Generate an AI insight, falling back to a deterministic summary on failure."""
    if not metrics.get("has_data"):
        return Insight(
            InsightStatus.NO_DATA,
            "Not enough data in the selected period to generate a commentary.",
        )

    prompt = build_prompt(metrics, config.currency)

    try:
        text = generate(prompt, model=model)
        if not text:
            return Insight(
                InsightStatus.EMPTY,
                _fallback_summary(metrics, "The model returned an empty response."),
            )
        return Insight(InsightStatus.OK, text)

    except OllamaOutOfMemory as exc:
        logger.warning("Ollama out of memory: %s", exc)
        return Insight(
            InsightStatus.MODEL_BUSY,
            _fallback_summary(
                metrics,
                "Not enough GPU memory to load the model right now. "
                "Another workload may be using it; try again later.",
            ),
        )

    except OllamaTimeout as exc:
        logger.warning("Ollama timed out: %s", exc)
        return Insight(
            InsightStatus.TIMEOUT,
            _fallback_summary(
                metrics,
                "The model did not respond in time. The first call after startup "
                "is slower while the model loads; try again.",
            ),
        )

    except OllamaError as exc:
        logger.error("Ollama call failed: %s", exc)
        return Insight(
            InsightStatus.UNAVAILABLE,
            _fallback_summary(metrics, str(exc)),
        )
