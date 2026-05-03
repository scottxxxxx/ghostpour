"""Public unsubscribe endpoint for marketing email links.

Targeted by the `unsubscribe` link we put in marketing emails. No auth:
the token in the URL self-identifies the user and is HMAC-signed with
JWT_SECRET so it can't be forged. On a valid token we flip
`users.marketing_opt_in = false` (source = unsubscribe_link) and
render a plain confirmation page.

The page is plain HTML by design — it has to render in any browser
the user's mail client opens, with no JS or web fonts.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.database import get_db
from app.services import marketing_opt_in as marketing

router = APIRouter()


_OK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Unsubscribed</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #fafafa; color: #1a1a1a; margin: 0; padding: 0; }}
  main {{ max-width: 480px; margin: 80px auto; padding: 32px 24px;
         background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; }}
  h1 {{ font-size: 22px; margin: 0 0 12px; }}
  p {{ font-size: 15px; line-height: 1.5; margin: 0 0 16px; color: #333; }}
  .muted {{ color: #777; font-size: 13px; }}
</style>
</head>
<body>
<main>
  <h1>You're unsubscribed</h1>
  <p>You won't receive marketing emails from us anymore.</p>
  <p class="muted">{detail}</p>
  <p class="muted">If this was a mistake, you can re-enable email
  in the app's Settings.</p>
</main>
</body>
</html>
"""


_ERROR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Unsubscribe failed</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #fafafa; color: #1a1a1a; margin: 0; padding: 0; }}
  main {{ max-width: 480px; margin: 80px auto; padding: 32px 24px;
         background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; }}
  h1 {{ font-size: 22px; margin: 0 0 12px; }}
  p {{ font-size: 15px; line-height: 1.5; margin: 0 0 16px; color: #333; }}
</style>
</head>
<body>
<main>
  <h1>Unsubscribe link is invalid</h1>
  <p>This link doesn't look right or has been tampered with. You can
  always disable marketing email from the app's Settings.</p>
</main>
</body>
</html>
"""


@router.get("/unsubscribe", include_in_schema=False)
async def unsubscribe(
    request: Request,
    token: str = "",
    db: aiosqlite.Connection = Depends(get_db),
):
    secret = request.app.state.settings.jwt_secret
    user_id = marketing.verify_unsubscribe_token(token, secret)
    if user_id is None:
        return HTMLResponse(content=_ERROR_HTML, status_code=400)

    changed = await marketing.set_marketing_opt_in(
        db, user_id,
        opt_in=False,
        source=marketing.SOURCE_UNSUBSCRIBE_LINK,
    )
    detail = (
        "Your preference was updated just now."
        if changed
        else "Your preference was already off — nothing to change."
    )
    return HTMLResponse(content=_OK_HTML.format(detail=detail), status_code=200)
