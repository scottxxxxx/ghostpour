"""Correct the 2026-08-28 fictional purchase on Scott's row.

NOT RUN. Waiting on Scott to choose an option. Run inside the prod
container:

    docker cp repair_scott_row.py ghostpour:/tmp/
    docker exec -w /app -e PYTHONPATH=/app ghostpour python /tmp/repair_scott_row.py --dry-run
    docker exec -w /app -e PYTHONPATH=/app ghostpour python /tmp/repair_scott_row.py --apply --entitlement=<admin|simulated|none>

What is wrong, established 2026-08-28:

  users.fa4d903c.original_transaction_id = '0'
  subscription_events 2026-08-28T13:24:00Z  subscribed  free -> pro  $14.99  Production

Apple's Production Notification History for 08-27..08-29 holds exactly
one notification, the EXPIRED/VOLUNTARY for his old trial, and no
purchase. Scott has confirmed he was running from Xcode against the
local ShoulderSurf.storekit configuration. So the purchase did not
happen, and MRR is counting $14.99/mo that does not exist.

MRR replays `to_tier` from the event log (subscriptions.mrr_trend), and
`user_environments` takes the LATEST event carrying an environment. So
the correction has to be an appended event, not an edit: the log is
append-only by design and rewriting it would destroy the record of what
we believed and when.

The `'0'` goes to NULL rather than back to 120003884095739, because that
trial subscription genuinely expired on 08-28 and is not his current
state. NULL is "we do not know", which is true. Note this takes him out
of the reconcile sweep, which selects on original_transaction_id being
present, so nothing will try an App Store lookup for him until a real
purchase binds a real id.

ENTITLEMENT is a separate decision from REVENUE, and only the revenue
half is a correctness question:

  --entitlement=admin      tier='admin'. Keeps his access. is_paid_tier()
                           is true for admin so he still shows in the
                           active_paid HEADCOUNT, but TIER_PRICE_USD has
                           no admin entry so he adds $0.00 to MRR gross.
  --entitlement=simulated  tier='free' with simulated_tier='pro'. He sees
                           Pro, and he leaves both the revenue line and
                           the paid headcount. Cleanest for reporting.
  --entitlement=none       tier='free', no simulation. He is Free until
                           he makes a real purchase.
"""

import argparse
import asyncio
import sys


async def main(apply: bool, entitlement: str):
    import aiosqlite

    from app.services import subscriptions as subs

    USER = "fa4d903c-24c0-45d5-9fdb-b5496e32501b"

    db = await aiosqlite.connect("/app/data/cloudzap.db")
    db.row_factory = aiosqlite.Row
    try:
        row = await (await db.execute(
            "SELECT tier, is_trial, simulated_tier, original_transaction_id, "
            "monthly_cost_limit_usd FROM users WHERE id = ?", (USER,))).fetchone()
        if not row:
            print("user not found")
            return 1
        print("BEFORE:", dict(row))

        target_tier = {"admin": "admin", "simulated": "free", "none": "free"}[entitlement]
        sim = "pro" if entitlement == "simulated" else None

        print(f"\nWOULD: append reconciled/void_unverified_purchase pro -> {target_tier}")
        print(f"WOULD: users.tier = {target_tier!r}, simulated_tier = {sim!r}")
        print("WOULD: users.original_transaction_id = NULL")

        if not apply:
            print("\n--dry-run, nothing written")
            return 0

        await subs.record_subscription_event(
            db, user_id=USER,
            event_type="reconciled",
            subtype="void_unverified_purchase",
            from_tier="pro", to_tier=target_tier,
            source="reconciliation",
            price_usd=0.0,
            environment=None,
            raw={
                "reason": "Apple has no record of this purchase",
                "voided_event": "2026-08-28T13:24:00.362624+00:00",
                "evidence": "App Store Server API notification history "
                            "2026-08-27..29 Production: only EXPIRED for "
                            "120003884095739, no purchase",
                "cause": "Xcode run against local ShoulderSurf.storekit "
                         "configuration; confirmed by Scott 2026-08-28",
            },
            commit=False,
        )
        await db.execute(
            "UPDATE users SET tier = ?, simulated_tier = ?, "
            "original_transaction_id = NULL, is_trial = 0 WHERE id = ?",
            (target_tier, sim, USER))
        await db.commit()

        after = await (await db.execute(
            "SELECT tier, is_trial, simulated_tier, original_transaction_id "
            "FROM users WHERE id = ?", (USER,))).fetchone()
        print("\nAFTER:", dict(after))
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--entitlement", choices=["admin", "simulated", "none"],
                    required=True)
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        print("pass --dry-run or --apply")
        sys.exit(2)
    sys.exit(asyncio.run(main(a.apply, a.entitlement)))
