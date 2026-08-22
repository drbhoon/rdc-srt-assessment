"""Every admin-protected route must live at a path the platform gates.

Run from the app root:   python tools/check_admin_gate.py

WHY THIS EXISTS
---------------
On hr.rdcc.ai the admin surface is gated by PATH, not by anything in this app.
nginx matches

    location ~ ^/srt/(admin|api/admin)

and only inside that match does it ask the portal who the caller is and attach
X-Auth-Email. Off that path the header is deliberately cleared, so a caller
cannot forge one — which also means an admin-only endpoint sitting outside the
pattern can never be authorised. An SSO login sets no X-Admin-Token either,
because the console skips the password prompt, so there is no second credential
to fall back on.

/api/download-pdf/{session_id} was that endpoint. The dashboard loaded, the
table filled, Diagnose and Rescore worked — every one of those is under
/api/admin — and the download alone came back "Unauthorized — admin only".

Nothing in development reproduces it: there is no nginx and no SSO locally, so
the token path works and the app looks fine. This check is what stands in for
that missing environment, and it costs a second to run.

HOW IT DECIDES
--------------
It reads the app's own route table and the source of each handler, so it is
exact rather than a guess about naming: a route is admin-protected if its
handler calls _admin_identity. No route list to keep in step by hand.
"""
from __future__ import annotations

import inspect
import os
import sys

# The prefixes nginx gates, from the location block quoted above. If that
# pattern is ever widened or narrowed in rdc-hr-platform/nginx/default.conf,
# this tuple is the other half of the contract and has to move with it.
GATED_PREFIXES = ("/admin", "/api/admin")

# Imported for its routes only; give it enough environment to load.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-not-used-here")
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, os.getcwd())

from fastapi.routing import APIRoute  # noqa: E402
import main  # noqa: E402


def main_check() -> int:
    protected: list[tuple[str, str]] = []
    unreadable: list[str] = []

    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        try:
            source = inspect.getsource(route.endpoint)
        except (OSError, TypeError):
            unreadable.append(route.path)
            continue
        if "_admin_identity" in source:
            protected.append((route.path, route.endpoint.__name__))

    offenders = [(p, n) for p, n in protected if not p.startswith(GATED_PREFIXES)]

    print(f"admin-protected routes found: {len(protected)}")
    for path, name in sorted(protected):
        mark = "FAIL" if (path, name) in offenders else "ok"
        print(f"  [{mark:>4}] {path}   ({name})")

    if unreadable:
        print(f"\ncould not read the source of {len(unreadable)} route(s): "
              + ", ".join(sorted(unreadable)))

    if not protected:
        print("\nNo admin-protected routes found at all — the check itself is "
              "probably broken. Failing rather than reporting a clean run.")
        return 1

    if offenders:
        print("\nFAILED - these routes check for an admin but sit outside the "
              "paths nginx gates, so on hr.rdcc.ai they can never be authorised:")
        for path, name in offenders:
            print(f"    {path}   ({name})")
        print("\nMove each one under /api/admin (leave a 307 redirect at the old "
              "path if anything might still call it), or widen the location "
              "regex in rdc-hr-platform/nginx/default.conf and GATED_PREFIXES "
              "here to match.")
        return 1

    print("\nPASSED - every admin-protected route is inside the gated paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main_check())
