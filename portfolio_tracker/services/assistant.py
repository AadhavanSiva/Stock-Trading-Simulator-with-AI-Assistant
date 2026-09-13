"""The in-app assistant. The only module that talks to the Claude API.

It answers one question at a time about the data the app hands it. It
never reads the database or the market feed itself — the caller passes a
context block built from those — so what it can say is bounded by what the
app actually knows.

Every failure comes back as an Answer with a reason a person can act on,
never an exception. A chat panel that throws a stack trace at a beginner
is worse than no chat panel.
"""
import logging
from collections import namedtuple

import anthropic

from portfolio_tracker import config

log = logging.getLogger(__name__)

Answer = namedtuple("Answer", "ok text kind message")

# The fallbacks parameter re-runs a declined request on another model
# server-side. It is only accepted for these models, so a different
# ASSISTANT_MODEL simply goes without it rather than getting a 400.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1", "claude-fable-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_QUESTION_CHARS = 1000
MAX_EARLIER_TURNS = 3
_MAX_EARLIER_CHARS = 1500

# Frozen: no dates, names or figures, so every request shares this prefix
# and it is served from the prompt cache. Anything that varies goes in the
# user message, after it.
SYSTEM_PROMPT = """You are the guide inside Portfolio Tracker, a practice investing app. The people using it are beginners learning how investing works. Each account starts with $50,000 of pretend money, and they buy real companies at real market prices.

People open you from whatever page they are on and ask about a stock, their holdings, or an idea they have come across. Your job is to help them understand, so they can make their own decisions with clearer eyes.

What you have to work with

Each question arrives with an <app_data> block that the app builds from its own records: the current price, the person's position and average cost if they own the stock, their cash, and a summary of stored price history over several ranges. Treat those figures as the truth for this conversation, and take any number you cite from that block.

Anything not in the block, such as earnings, valuation ratios, recent news, analyst views or a company's latest products, you either do not have or know only from training data that may be out of date. If you draw on general background about a company, say it may not reflect recent events. Never produce a figure that is not in the data. Say you do not have it and, where useful, where a person would find it, such as the company's annual report or investor relations page.

Where the line is

This app teaches; it does not advise. Explain concepts, describe what the data shows about the past, and lay out the considerations and risks people weigh. Do not tell someone to buy, sell or hold, do not predict where a price is heading, and do not call something a good or bad investment. Past movement says nothing reliable about what comes next, and a beginner who hears a confident verdict from an assistant is likely to act on it.

When someone asks "should I buy this?" or "will it go up?", do not simply decline. Help them think it through: how much of their account it would be, how much the price has swung in the data, what they are hoping will happen and why. Make clear the decision is theirs.

How to write

Use plain language for someone who has never invested, and explain any unavoidable term in a few words the first time it appears. Keep answers short. A few short paragraphs is usually right; go longer only when the question needs it. Describe gains and losses in the same even tone, since neither is an achievement or a failure. No hype and no alarm.

Your reply appears as plain text in a narrow side panel, so do not use markdown: no headings, tables, bold text or bullet symbols. Separate ideas with a blank line."""


_client = None


def _get_client():
    """One shared client. Constructing it does not check credentials — the
    SDK resolves those at request time — so this cannot fail on its own."""
    global _client
    if _client is None:
        # A person is waiting on this. Keep the worst case bounded: the SDK
        # retries on timeout, so wall-clock can reach timeout * (retries + 1).
        _client = anthropic.Anthropic(timeout=90.0, max_retries=1)
    return _client


def _clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def build_user_message(context, question, earlier=()):
    """Assemble the one user turn: app data, recent exchanges, the question.

    Earlier exchanges are quoted inside this single user message rather than
    replayed as assistant turns. Each request stays self-contained, the app
    data is always current, and there is no stored model history to keep
    consistent between requests.
    """
    parts = [f"<app_data>\n{context.strip()}\n</app_data>"]

    recent = [turn for turn in (earlier or ()) if turn.get("question") and turn.get("answer")]
    recent = recent[-MAX_EARLIER_TURNS:]
    if recent:
        lines = []
        for turn in recent:
            lines.append(f"Question: {_clip(turn['question'], MAX_QUESTION_CHARS)}")
            lines.append(f"Your answer: {_clip(turn['answer'], _MAX_EARLIER_CHARS)}")
        parts.append(
            "<earlier_in_this_conversation>\n"
            + "\n\n".join(lines)
            + "\n</earlier_in_this_conversation>"
        )

    parts.append(f"<question>\n{question.strip()}\n</question>")
    return "\n\n".join(parts)


def ask(context, question, earlier=()):
    """Answer one question against the given app data. Never raises."""
    if not config.ASSISTANT_ENABLED:
        return Answer(False, "", "disabled", "The assistant is switched off for this app.")

    question = (question or "").strip()
    if not question:
        return Answer(False, "", "invalid", "Type a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        return Answer(
            False, "", "invalid",
            f"That question is too long. Keep it under {MAX_QUESTION_CHARS} characters.",
        )

    request = {
        "model": config.ASSISTANT_MODEL,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": config.ASSISTANT_EFFORT},
        "system": [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{
            "role": "user",
            "content": build_user_message(context, question, earlier),
        }],
    }
    if config.ASSISTANT_MODEL in _FALLBACK_MODELS:
        request["betas"] = [_FALLBACK_BETA]
        request["fallbacks"] = "default"

    try:
        response = _get_client().beta.messages.create(**request)
    except TypeError as exc:
        # No credentials at all surfaces as a TypeError from the SDK, not an
        # API error, so the chain below would never see it.
        if "authentication" in str(exc).lower():
            return _not_configured()
        raise
    except anthropic.AuthenticationError:
        return Answer(
            False, "", "not_configured",
            "The assistant's API key was rejected. Check ANTHROPIC_API_KEY in "
            "your .env file, then restart the server.",
        )
    except anthropic.PermissionDeniedError:
        return Answer(
            False, "", "not_configured",
            "The API key works but is not allowed to use this model. Check the "
            "key's workspace permissions, or set ASSISTANT_MODEL in .env.",
        )
    except anthropic.RateLimitError:
        return Answer(
            False, "", "busy",
            "The assistant is getting too many questions right now. Wait a "
            "minute and ask again.",
        )
    except anthropic.BadRequestError as exc:
        log.warning("assistant bad request: %s", exc)
        return Answer(
            False, "", "failed",
            "That question could not be processed. Try rephrasing it.",
        )
    except anthropic.APITimeoutError:
        return Answer(
            False, "", "failed",
            "The assistant took too long to answer. Try a shorter or simpler question.",
        )
    except anthropic.APIConnectionError:
        return Answer(
            False, "", "failed",
            "Could not reach the assistant. Check your internet connection and try again.",
        )
    except anthropic.APIStatusError as exc:
        log.warning("assistant API error %s: %s", exc.status_code, exc)
        if exc.status_code >= 500:
            message = "The assistant service is having trouble. Try again in a few minutes."
        else:
            message = "Something went wrong asking the assistant. Try again."
        return Answer(False, "", "failed", message)

    log.debug(
        "assistant request %s: cache read %s, cache write %s, input %s",
        getattr(response, "_request_id", None),
        getattr(response.usage, "cache_read_input_tokens", None),
        getattr(response.usage, "cache_creation_input_tokens", None),
        getattr(response.usage, "input_tokens", None),
    )

    # A decline — by the requested model and any fallback — arrives as a
    # normal response, so check before reading content.
    if response.stop_reason == "refusal":
        return Answer(
            False, "", "refused",
            "The assistant can't help with that question. Try asking what the "
            "numbers on this page mean, or how a term like average cost works.",
        )

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    if not text:
        return Answer(
            False, "", "failed",
            "The assistant didn't produce an answer. Try asking again.",
        )
    return Answer(True, text, "answered", "")


def _not_configured():
    return Answer(
        False, "", "not_configured",
        "The assistant isn't set up yet. Add ANTHROPIC_API_KEY=your_key to the "
        ".env file (create a key at console.anthropic.com), then restart the server.",
    )
