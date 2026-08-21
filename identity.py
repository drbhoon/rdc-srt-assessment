"""Client for the portal's identity resolver.

Every app on the platform links its records to one shared person_id rather than
keeping its own idea of who somebody is. This turns the e-mail collected at the
door into that id.

SRT has two doors, and they get opposite treatment.

The EMPLOYEE door is roll-bound: it asks with require_internal, and somebody
who is not on the rolls is refused rather than invented. Both the employee code
and the e-mail must point at the same person.

The EXTERNAL door is for recruitment, where by definition nobody is on the
master yet. It asks with require_internal=False and create=True, so the address
is registered as an external person on the shared spine — the same person a
later DISC or recruitment record will resolve to, and the same person they
become when they are hired.

The difference that matters is what an unreachable portal means. For the
employee door it is a refusal, because the check is the point. For the external
door it is not, because there was nothing to check against: the candidate is
admitted and the session records the address without a person_id.

Uses urllib from the standard library on purpose. The only HTTP client in the
image is httpx, and that arrives transitively through `anthropic` — a
dependency this module would then silently rely on surviving an unrelated
version bump. One JSON POST does not justify that.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

MASTER_API_URL = (os.environ.get("MASTER_API_URL") or "").rstrip("/")
MASTER_API_KEY = os.environ.get("MASTER_API_KEY") or ""

# A slow portal must not hold the candidate's browser open at the one moment
# they are trying to start a timed assessment.
TIMEOUT_SECONDS = 5


def identity_configured() -> bool:
    """False on Railway and local dev, where the portal does not exist."""
    return bool(MASTER_API_URL and MASTER_API_KEY)


def resolve_employee(employee_code: str, email: str) -> dict:
    """Resolve a candidate who must be a known employee, with BOTH fields agreeing.

    Same result shape as resolve_person.

    The e-mail is looked up ALONE and the returned employee code is then
    compared with the one typed. Passing the code to the resolver would ask it
    to ATTACH an unrecognised address to that employee — which is the right
    behaviour for an HR screen adding somebody's personal address, and quite
    wrong here: it would let a candidate type a colleague's employee code
    beside their own e-mail and have a 75-minute assessment recorded against
    the colleague.

    Requiring both to point at the same person means a mistyped code is caught
    at the door rather than discovered in somebody else's report.
    """
    code = (employee_code or "").strip()
    if not code:
        return {"ok": False, "reason": "not_found", "message": "Employee code is required."}

    result = resolve_person(email=email, require_internal=True, create=False)
    if not result["ok"]:
        return result

    found = (result["person"].get("employee_code") or "").strip()
    if found.casefold() != code.casefold():
        # Deliberately does NOT reveal whose code was actually typed.
        return {
            "ok": False,
            "reason": "not_found",
            "message": "That employee code does not match the employee code held "
                       "against this e-mail address. Please check both with HR.",
        }
    return result


def resolve_person(email: str, employee_code: str | None = None,
                   name: str | None = None,
                   require_internal: bool = True,
                   create: bool = True) -> dict:
    """Resolve an e-mail address to a person.

    Returns one of:
        {"ok": True,  "person": {...}}
        {"ok": False, "reason": "unconfigured" | "not_found" | "unavailable",
         "message": str | None}

    A result rather than an exception, and "no such person" kept separate from
    "could not ask": the two need opposite handling, and an unreachable portal
    must never be allowed to look like a rejection.
    """
    if not identity_configured():
        return {"ok": False, "reason": "unconfigured", "message": None}
    if not email:
        return {"ok": False, "reason": "not_found", "message": "No e-mail address given."}

    payload = {"email": email, "require_internal": require_internal, "create": create}
    if employee_code:
        payload["employee_code"] = employee_code
    if name:
        payload["name"] = name

    request = urllib.request.Request(
        f"{MASTER_API_URL}/api/identity/resolve",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-master-key": MASTER_API_KEY},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return {"ok": True, "person": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as err:
        if err.code == 404:
            try:
                body = json.loads(err.read().decode("utf-8"))
            except Exception:
                body = {}
            return {"ok": False, "reason": "not_found", "message": body.get("error")}
        logger.error("[identity] portal returned %s", err.code)
        return {"ok": False, "reason": "unavailable", "message": None}
    except Exception as err:                      # timeout, DNS, refused
        logger.error("[identity] resolve failed: %s", err)
        return {"ok": False, "reason": "unavailable", "message": None}
