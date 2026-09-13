"""The in-app assistant. The only module that talks to the Gemini API.

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

import httpx
from google import genai
from google.genai import errors, types

from portfolio_tracker import config

log = logging.getLogger(__name__)

Answer = namedtuple("Answer", "ok text kind message")

MAX_QUESTION_CHARS = 1000
MAX_EARLIER_TURNS = 3
_MAX_EARLIER_CHARS = 1500

# Thinking tokens count toward the output limit on Gemini, so this leaves
# room to reason and still finish a short answer.
_MAX_OUTPUT_TOKENS = 8192
_THINKING_LEVELS = ("low", "medium", "high")

# Finish reasons that mean the answer was withheld, not that it ended
# normally. Any text alongside them is discarded rather than shown.
_WITHHELD = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}

# Frozen: no dates, names or figures, so every request carries the same
# instructions. Anything that varies goes in the user contents.
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
    """One shared client, created on first use.

    Unlike some SDKs, google-genai checks for a key when the client is
    constructed and raises ValueError if there is none. A failed attempt
    is not cached, so adding a key and restarting is all it takes.
    """
    global _client
    if _client is None:
        # A person is waiting on this; timeout is in milliseconds.
        _client = genai.Client(http_options=types.HttpOptions(timeout=90_000))
    return _client


def _clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def build_user_message(context, question, earlier=()):
    """Assemble the one user turn: app data, recent exchanges, the question.

    Earlier exchanges are quoted inside this single message rather than
    replayed as model turns. Each request stays self-contained, the app data
    is always current, and no stored conversation has to be kept in sync.
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


def _request_config():
    thinking = config.ASSISTANT_THINKING
    if thinking not in _THINKING_LEVELS:
        # An unsupported level is a 400 from the API; fall back rather than
        # break the panel over a typo in .env.
        log.warning("ASSISTANT_THINKING=%r is not low/medium/high; using medium", thinking)
        thinking = "medium"

    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
        # No tools are passed; switching automatic function calling off
        # also stops the SDK logging a warning on every request.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _reason_name(value):
    """Enum or string -> 'SAFETY'. The SDK returns enums; be tolerant."""
    if value is None:
        return None
    return getattr(value, "name", str(value)).upper()


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

    try:
        client = _get_client()
    except ValueError:
        return _not_configured()

    try:
        response = client.models.generate_content(
            model=config.ASSISTANT_MODEL,
            contents=build_user_message(context, question, earlier),
            config=_request_config(),
        )
    except errors.ClientError as exc:
        return _client_error(exc)
    except errors.ServerError as exc:
        log.warning("assistant server error %s: %s", exc.code, exc.message)
        return Answer(
            False, "", "failed",
            "The assistant service is having trouble. Try again in a few minutes.",
        )
    except errors.APIError as exc:
        log.warning("assistant API error %s: %s", exc.code, exc.message)
        return Answer(False, "", "failed", "Something went wrong asking the assistant. Try again.")
    # Network failures surface as raw httpx exceptions, not API errors.
    # TimeoutException is itself a RequestError, so it is checked first.
    except httpx.TimeoutException:
        return Answer(
            False, "", "failed",
            "The assistant took too long to answer. Try a shorter or simpler question.",
        )
    except httpx.RequestError:
        return Answer(
            False, "", "failed",
            "Could not reach the assistant. Check your internet connection and try again.",
        )

    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        log.info("assistant prompt blocked: %s", _reason_name(feedback.block_reason))
        return _refused()

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return Answer(False, "", "failed", "The assistant didn't produce an answer. Try asking again.")

    candidate = candidates[0]
    if _reason_name(getattr(candidate, "finish_reason", None)) in _WITHHELD:
        return _refused()

    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    # Thought summaries are only returned when asked for, but never show
    # one as if it were the answer.
    text = "".join(
        part.text for part in parts
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    ).strip()

    if not text:
        if _reason_name(getattr(candidate, "finish_reason", None)) == "MAX_TOKENS":
            return Answer(
                False, "", "failed",
                "The assistant ran out of room before answering. Try a narrower question.",
            )
        return Answer(False, "", "failed", "The assistant didn't produce an answer. Try asking again.")

    return Answer(True, text, "answered", "")


def _client_error(exc):
    message = str(exc.message or "")
    # An invalid key is a 400 INVALID_ARGUMENT on Gemini, not a 401, so the
    # status code alone would send someone off to rephrase their question.
    if "api key not valid" in message.lower() or "API_KEY_INVALID" in str(exc.details or ""):
        return Answer(
            False, "", "not_configured",
            "The Gemini API key was rejected. Check GEMINI_API_KEY in your .env "
            "file, then restart the server.",
        )
    if exc.code in (401, 403):
        return Answer(
            False, "", "not_configured",
            "The Gemini API key isn't allowed to use this model. Check the key's "
            "project in Google AI Studio, or set ASSISTANT_MODEL in .env.",
        )
    if exc.code == 404:
        return Answer(
            False, "", "not_configured",
            f"The model '{config.ASSISTANT_MODEL}' isn't available. Set "
            "ASSISTANT_MODEL in .env to a current Gemini model.",
        )
    if exc.code == 429:
        return Answer(
            False, "", "busy",
            "The assistant has hit its usage limit for now. Wait a minute and ask "
            "again — free Gemini keys have low per-minute limits.",
        )
    log.warning("assistant client error %s: %s", exc.code, message)
    return Answer(False, "", "failed", "That question could not be processed. Try rephrasing it.")


def _refused():
    return Answer(
        False, "", "refused",
        "The assistant can't help with that question. Try asking what the "
        "numbers on this page mean, or how a term like average cost works.",
    )


def _not_configured():
    return Answer(
        False, "", "not_configured",
        "The assistant isn't set up yet. Add GEMINI_API_KEY=your_key to the .env "
        "file (create a key at aistudio.google.com/apikey), then restart the server.",
    )
