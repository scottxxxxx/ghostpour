"""Who says this purchase happened: the client, or Apple?

Until this module, the answer was the client. `/v1/verify-receipt` read
`product_id` off the request body, mapped it to a tier and granted it.
The docstring said so out loud ("For MVP: trusts the product_id from the
authenticated client"), and on 2026-08-28 that came due: Scott ran
Shoulder Surf from Xcode with the local `ShoulderSurf.storekit`
configuration, tapped subscribe, and GhostPour granted Pro and booked
$14.99 of MRR for a transaction that never existed. Apple's own
Notification History for that window holds exactly one notification, the
EXPIRED for his old trial, and no purchase at all. The positive control
is in the same query: a real purchase by another user on 08-23 shows a
clean SUBSCRIBED/INITIAL_BUY. So the instrument works, and it says the
money was never spent.

The tell was already in the database. A locally synthesized StoreKit
transaction reports `originalID == 0`, which the client dutifully sent
and which we dutifully stored over a perfectly good id, so a real
subscriber's row ended up holding `0` for the field Apple's server
notifications are matched on.

What actually verifies a purchase is the JWS Apple signs. Xcode's local
StoreKit configuration signs its transactions too, but with a local test
certificate, so a chain check against Apple Root CA G3 refuses it. That
is the whole fix: stop asking the client what it bought and read it off
something Apple signed.

THE BUNDLE ID CHECK IS NOT DECORATION, and it is the reason this does
not simply call `decode_and_verify_jws` and trust the result.
`_verify_x5c_chain` takes a `bundle_id` parameter and never once reads
it, and `decode_and_verify_jws` never looks at the payload's `bundleId`
either. For the webhook that is survivable, because Apple pushes those
to us. Here the JWS arrives from a client, so without this check any
signed transaction from any App Store app on earth would verify and buy
its holder a Pro subscription. Verify the property, do not just name it.

Rollout is a dial, not a flag day. There are live paying subscribers on
builds that have never sent `signed_transaction`, so refusing unsigned
calls today would strip Pro from people who paid for it. The dial
defaults to off: we verify when we can, we harden the id either way, and
enforcement flips the moment Shoulder Surf's build carrying the field is
installed. That flip is Scott's to make, from the dashboard, without a
deploy.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("ghostpour.receipt_verification")

SLUG = "verify-receipt"
FIELD = "require_signed_transaction"

# Apple transaction identifiers are the decimal form of a positive
# integer. `str.isdigit()` is NOT this test: it is true for Arabic-Indic
# and other unicode digits, so it would pass a string no Apple id could
# ever be. Pin the carrier string.
_ALL_ASCII_DIGITS = re.compile(r"\A[0-9]+\Z")


def require_signed_transaction(remote_configs: dict, settings) -> bool:
    """Whether an unverifiable verify-receipt call should be refused.

    Off unless the served dial says otherwise. A malformed dial value is
    ignored and logged rather than guessed at, because guessing `True`
    here locks paying customers out of the tier they bought.
    """
    raw = ((remote_configs or {}).get(SLUG) or {}).get(FIELD)
    if raw is None:
        return False
    if not isinstance(raw, bool):
        logger.warning(
            "require_signed_transaction_dial_ignored reason=not_a_bool value=%r", raw)
        return False
    return raw


def is_plausible_transaction_id(value: object) -> bool:
    """True for something that could be an Apple transaction id.

    Rejects the two shapes we have actually seen do damage: `'0'` from a
    locally synthesized StoreKit transaction, and absence. Anything that
    is not a run of ASCII digits is not an Apple id.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not _ALL_ASCII_DIGITS.match(candidate):
        return False
    return int(candidate) != 0


def allowed_bundle_ids(settings) -> set[str]:
    """The bundle ids this gateway will accept a transaction for.

    `apple_bundle_id` may be a comma-joined list because one gateway
    serves several apps, so a single-value comparison would reject the
    other apps' real purchases.
    """
    raw = getattr(settings, "apple_bundle_id", "") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


@dataclass(frozen=True)
class TransactionIdentity:
    """What we are willing to say about a purchase, and on whose word.

    `verified` is the load-bearing field: True means every other field
    was read out of a payload Apple signed for one of our bundle ids.
    False means the client asserted them.
    """

    original_transaction_id: str | None
    transaction_id: str | None
    environment: str | None
    product_id: str | None
    verified: bool
    reject_reason: str | None = None

    @property
    def usable_original_id(self) -> str | None:
        """The id to store, or None to leave whatever is already there.

        None is the important case. It means we were handed something
        that is not an Apple id, and the correct response is to keep the
        good id already on the row rather than overwrite it with junk.
        """
        return self.original_transaction_id


def verify_signed_transaction(jws: str, bundle_ids: set[str]) -> dict:
    """Decode a client-supplied signed transaction, strictly.

    Raises AppleJWSError unless Apple's root signed the chain AND the
    payload names one of our bundle ids.
    """
    from app.services.apple_notifications import AppleJWSError, decode_and_verify_jws

    payload = decode_and_verify_jws(jws, ",".join(sorted(bundle_ids)))
    claimed = payload.get("bundleId")
    if not claimed or claimed not in bundle_ids:
        raise AppleJWSError(
            f"signed transaction is for bundleId {claimed!r}, "
            f"which is not one of ours"
        )
    return payload


def resolve_identity(body, settings) -> TransactionIdentity:
    """Work out who this transaction really is.

    Prefers Apple's signature. Falls back to the client's own fields,
    marked unverified, so that today's installed builds keep working
    while the dial is off.
    """
    bundle_ids = allowed_bundle_ids(settings)
    signed = getattr(body, "signed_transaction", None)

    if signed:
        try:
            payload = verify_signed_transaction(signed, bundle_ids)
        except Exception as e:
            # Do not fall through to the client's claim silently. The
            # caller decides whether an unverifiable receipt is fatal;
            # our job is to make sure it can tell.
            logger.warning("verify-receipt: signed_transaction rejected: %s", e)
            return TransactionIdentity(
                original_transaction_id=None,
                transaction_id=None,
                environment=None,
                product_id=None,
                verified=False,
                reject_reason=f"signature_rejected: {e}",
            )

        otid = payload.get("originalTransactionId")
        txn = payload.get("transactionId")
        env = payload.get("environment")
        return TransactionIdentity(
            original_transaction_id=otid if is_plausible_transaction_id(otid) else None,
            transaction_id=txn if is_plausible_transaction_id(txn) else None,
            environment=env if env in ("Production", "Sandbox") else None,
            product_id=payload.get("productId"),
            verified=True,
            reject_reason=(
                None if is_plausible_transaction_id(otid)
                else f"apple_signed_but_implausible_otid: {otid!r}"
            ),
        )

    # Unsigned: the client's word. Keep it, but never let an implausible
    # id through to the column Apple's notifications are matched on.
    claimed_otid = getattr(body, "transaction_id", None)
    claimed_txn = getattr(body, "current_transaction_id", None) or claimed_otid
    ok = is_plausible_transaction_id(claimed_otid)
    return TransactionIdentity(
        original_transaction_id=claimed_otid if ok else None,
        transaction_id=claimed_txn if is_plausible_transaction_id(claimed_txn) else None,
        environment=None,
        product_id=getattr(body, "product_id", None),
        verified=False,
        reject_reason=None if ok else f"implausible_transaction_id: {claimed_otid!r}",
    )
