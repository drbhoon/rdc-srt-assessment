"""Plain-language messages for AI-account problems, shared by Plant Manager and PQI.

When the Anthropic account runs out of credit, every call fails, and before
this the console showed either a raw API error dump or "ReplyFormatError ..."
per question — which read as a scoring bug and sent people to Rescore over and
over. These problems are not in the assessment and not in the answers; they
are fixed in the Anthropic console, after which Rescore resumes where the run
stopped. This module recognises them from the error text and says so.

Every message starts with "Anthropic", which the console uses to show a short
reason under the Failed badge.
"""
from __future__ import annotations

CREDIT = ("Anthropic credit exhausted — the AI account has no credit left, so scoring stopped. "
          "Top up at console.anthropic.com (Plans & Billing), then press Rescore: saved answers "
          "and completed evaluations are kept.")
USAGE_LIMIT = ("Anthropic usage limit reached — the API key's spending limit for this period is used up. "
               "Raise it at console.anthropic.com (Limits), then press Rescore: saved answers and "
               "completed evaluations are kept.")
BAD_KEY = ("Anthropic API key rejected — the server's ANTHROPIC_API_KEY is missing, mistyped or revoked. "
           "Correct it in the server .env, recreate srt, then press Rescore: saved answers and completed "
           "evaluations are kept.")

_PATTERNS = (
    (CREDIT, ("credit balance is too low", "purchase credits", "insufficient credit", "billing")),
    (USAGE_LIMIT, ("usage limit", "spend limit", "spending limit")),
    (BAD_KEY, ("invalid x-api-key", "authentication_error", "authenticationerror", "invalid api key",
               "permission_error")),
)


def account_problem(error) -> str | None:
    """The plain message for an AI-account problem, or None if `error` is something else.

    `error` may be an exception or text; several texts (e.g. one per failed
    question) can be passed as a list.
    """
    texts = error if isinstance(error, (list, tuple, set)) else [error]
    joined = " ".join(f"{type(t).__name__} {t}" if isinstance(t, BaseException) else str(t or "")
                      for t in texts).lower()
    for message, needles in _PATTERNS:
        if any(n in joined for n in needles):
            return message
    return None
