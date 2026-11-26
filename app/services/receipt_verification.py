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

TRUSTED LOCAL TEST CERTIFICATES (2026-11-25). Closing the hole means
turning the dial on, and the dial refuses Scott's own Xcode runs, because
a local StoreKit configuration signs with Xcode's "StoreKit Test"
certificate: one self-signed cert, no chain. So the same served doc can
list the SHA-256 fingerprints of the test certificates the team owns
(`trusted_storekit_test_certs`). A JWS whose x5c is EXACTLY one
certificate, whose fingerprint is listed, and whose signature verifies
against that certificate's key is accepted with environment "Xcode". The
fingerprint alone proves nothing (a certificate is public, anyone can
paste it into a header); the signature check is what proves the caller
holds the private key, which lives in the Xcode install that made it.
"Xcode" is never revenue, never a purchase alert, and its transaction
ids are a per-device counter, so the caller never binds them to an
account the way it binds an Apple id.

THE APPLE LOOKUP (2026-11-25). Builds before 1.17 send no JWS at all.
With the dial on, an unsigned call with a plausible id is checked against
Apple's own record through the App Store Server API client we already
have. It is weaker than a JWS in one way that matters: it proves the
subscription exists and is active for our app, NOT that this caller is
the one who bought it. So a lookup grants the tier and never moves a plan
off another account (see `via_lookup`).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("ghostpour.receipt_verification")

SLUG = "verify-receipt"
FIELD = "require_signed_transaction"
TRUSTED_CERTS_FIELD = "trusted_storekit_test_certs"

_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
_FINGERPRINT_SEPARATORS = re.compile(r"[:\s]")

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


def normalize_fingerprint(value: object) -> str | None:
    """A SHA-256 fingerprint as 64 lowercase hex characters, or None.

    `openssl x509 -fingerprint` prints upper-case pairs joined by colons,
    Keychain Access prints them with spaces, and a hash tool prints bare
    lowercase. All three are the same fingerprint, so case, colons and
    whitespace are ignored. Anything that is not 64 hex characters after
    that is not a SHA-256 and is refused.
    """
    if not isinstance(value, str):
        return None
    candidate = _FINGERPRINT_SEPARATORS.sub("", value).lower()
    return candidate if _SHA256_HEX.match(candidate) else None


def trusted_storekit_test_certs(remote_configs: dict | None) -> frozenset[str]:
    """The fingerprints of the StoreKit Test certificates the team owns.

    Empty unless the served doc lists some. A value that is not a list
    trusts nothing, and an entry that is not a SHA-256 is skipped; both
    are logged and neither raises, because a typo in a config doc must
    not take verify-receipt down or, worse, trust something by accident.
    """
    raw = ((remote_configs or {}).get(SLUG) or {}).get(TRUSTED_CERTS_FIELD)
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        logger.warning(
            "trusted_storekit_test_certs_ignored reason=not_a_list type=%s",
            type(raw).__name__)
        return frozenset()
    trusted = set()
    for index, entry in enumerate(raw):
        fingerprint = normalize_fingerprint(entry)
        if fingerprint is None:
            logger.warning(
                "trusted_storekit_test_certs_entry_ignored index=%d "
                "reason=not_64_hex_characters", index)
            continue
        trusted.add(fingerprint)
    return frozenset(trusted)


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
    # What Apple said about money on this transaction (price_paid, currency,
    # offer_type, offer_discount_type, auto_renew_status), read off the
    # signed payload only. Empty when unsigned: the client's word about
    # price is worth nothing and is not recorded (2026-09-10).
    money: dict = field(default_factory=dict)
    # SHA-256 of the one certificate a rejected single-certificate JWS
    # carried, so the owner can see which StoreKit Test certificate to
    # list. A fingerprint of a public certificate is not a secret.
    cert_sha256: str | None = None
    # True when `verified` rests on asking Apple about an id the client
    # NAMED, not on a JWS the client HELD. Apple confirmed the
    # subscription is real; nothing proved this caller bought it. The
    # tier is granted, a plan is never moved off another account on it.
    via_lookup: bool = False

    @property
    def is_xcode(self) -> bool:
        """Verified against a trusted local StoreKit Test certificate."""
        from app.services.subscriptions import XCODE
        return self.verified and self.environment == XCODE

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
    from app.services.apple_notifications import decode_and_verify_jws

    payload = decode_and_verify_jws(jws, ",".join(sorted(bundle_ids)))
    _require_our_bundle(payload, bundle_ids)
    return payload


def _require_our_bundle(payload: dict, bundle_ids: set[str]) -> None:
    from app.services.apple_notifications import AppleJWSError

    claimed = payload.get("bundleId")
    if not claimed or claimed not in bundle_ids:
        raise AppleJWSError(
            f"signed transaction is for bundleId {claimed!r}, "
            f"which is not one of ours"
        )


def single_cert_fingerprint(jws: str) -> str | None:
    """SHA-256 (hex, of the DER) of the certificate in a ONE-certificate
    x5c, or None for any other shape. Never raises: it runs on input that
    has already failed verification."""
    from app.services.apple_notifications import split_jws

    try:
        header, _, _, _ = split_jws(jws)
        x5c = header["x5c"]
        if len(x5c) != 1 or not isinstance(x5c[0], str):
            return None
        return hashlib.sha256(base64.b64decode(x5c[0], validate=True)).hexdigest()
    except Exception:
        return None


def verify_storekit_test_transaction(jws: str, bundle_ids: set[str],
                                     trusted: frozenset[str]) -> dict:
    """Decode a transaction signed by a trusted local StoreKit Test cert.

    Raises AppleJWSError unless ALL of these hold: the x5c is exactly one
    certificate, its fingerprint is in `trusted`, the JWS signature
    verifies against that certificate's P-256 key, and the payload names
    one of our bundle ids. Parsing and the signature check are the Apple
    verifier's own (`split_jws`, `verify_es256_signature`), not a copy.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.services.apple_notifications import (
        AppleJWSError, decode_jws_payload, split_jws, verify_es256_signature)

    header, header_b64, payload_b64, signature_b64 = split_jws(jws)
    x5c = header["x5c"]
    if len(x5c) != 1 or not isinstance(x5c[0], str):
        raise AppleJWSError(
            f"not a local test transaction: {len(x5c)} certs in x5c (need exactly 1)")
    try:
        der = base64.b64decode(x5c[0], validate=True)
    except Exception as e:
        raise AppleJWSError(f"Failed to decode x5c cert: {e}")
    if hashlib.sha256(der).hexdigest() not in trusted:
        raise AppleJWSError("single certificate is not a trusted StoreKit Test certificate")
    try:
        cert = x509.load_der_x509_certificate(der)
    except Exception as e:
        raise AppleJWSError(f"Failed to parse x5c cert: {e}")
    key = cert.public_key()
    if not (isinstance(key, ec.EllipticCurvePublicKey)
            and isinstance(key.curve, ec.SECP256R1)):
        raise AppleJWSError("trusted certificate does not hold a P-256 key")
    # The check that matters. A listed fingerprint says WHICH certificate
    # is in the header and nothing about who put it there.
    verify_es256_signature(cert, header_b64, payload_b64, signature_b64)
    payload = decode_jws_payload(payload_b64)
    if not isinstance(payload, dict):
        raise AppleJWSError("JWS payload is not an object")
    _require_our_bundle(payload, bundle_ids)
    return payload


def resolve_identity(body, settings, remote_configs: dict | None = None) -> TransactionIdentity:
    """Work out who this transaction really is.

    Prefers Apple's signature, then a trusted local StoreKit Test
    certificate. Falls back to the client's own fields, marked
    unverified, so that today's installed builds keep working while the
    dial is off.
    """
    bundle_ids = allowed_bundle_ids(settings)
    signed = getattr(body, "signed_transaction", None)

    if signed:
        try:
            payload = verify_signed_transaction(signed, bundle_ids)
        except Exception as e:
            from app.services.subscriptions import UNVERIFIED, XCODE
            # Apple's chain refused it. A one-certificate chain is what
            # Xcode's local StoreKit testing produces, and the only shape
            # the trusted list can rescue.
            cert_sha256 = single_cert_fingerprint(signed)
            if cert_sha256:
                trusted = trusted_storekit_test_certs(remote_configs)
                if cert_sha256 in trusted:
                    try:
                        local = verify_storekit_test_transaction(signed, bundle_ids, trusted)
                    except Exception as local_error:
                        logger.warning(
                            "verify-receipt: trusted StoreKit Test certificate "
                            "%s but the transaction was refused: %s",
                            cert_sha256, local_error)
                    else:
                        otid = local.get("originalTransactionId")
                        txn = local.get("transactionId")
                        from app.services.subscriptions import money_fields_from_apple
                        return TransactionIdentity(
                            original_transaction_id=otid if is_plausible_transaction_id(otid) else None,
                            transaction_id=txn if is_plausible_transaction_id(txn) else None,
                            environment=XCODE,
                            product_id=local.get("productId"),
                            verified=True,
                            money=money_fields_from_apple(local),
                            # No reject_reason for an implausible id: a local
                            # StoreKit configuration numbers from 0, so "0" is
                            # an honest Xcode id and not something to alert on.
                            reject_reason=None,
                        )
                # Discoverability: this is the value to list. The JWS
                # itself is never logged.
                logger.info(
                    "verify-receipt: single-certificate chain refused, "
                    "cert_sha256=%s (list it under %s.%s to trust this "
                    "StoreKit Test certificate)",
                    cert_sha256, SLUG, TRUSTED_CERTS_FIELD)
            # Do not fall through to the client's claim silently. The
            # caller decides whether an unverifiable receipt is fatal;
            # our job is to make sure it can tell.
            logger.warning("verify-receipt: signed_transaction rejected: %s", e)
            return TransactionIdentity(
                original_transaction_id=None,
                transaction_id=None,
                # Explicit, never None: None makes the caller guess from the
                # account's history, which is how a local StoreKit-testing
                # purchase became "Production" (2026-10-20).
                environment=UNVERIFIED,
                product_id=None,
                verified=False,
                reject_reason=f"signature_rejected: {e}",
                cert_sha256=cert_sha256,
            )

        otid = payload.get("originalTransactionId")
        txn = payload.get("transactionId")
        env = payload.get("environment")
        from app.services.subscriptions import money_fields_from_apple
        return TransactionIdentity(
            original_transaction_id=otid if is_plausible_transaction_id(otid) else None,
            transaction_id=txn if is_plausible_transaction_id(txn) else None,
            environment=env if env in ("Production", "Sandbox") else None,
            product_id=payload.get("productId"),
            verified=True,
            money=money_fields_from_apple(payload),
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


async def confirm_unsigned_with_apple(identity: TransactionIdentity, body,
                                      settings) -> TransactionIdentity | None:
    """Ask Apple about an UNSIGNED claim. A verified identity, or None.

    For builds before 1.17, which never send the JWS. The caller only
    uses this when the dial is on. A `signed_transaction` that was
    present and refused is never rescued here: that is checked on the
    request itself, first, and does not lean on the refused identity
    happening to carry no id.

    None (so the caller's 400 stands) unless Apple's App Store Server
    API returns an ACTIVE subscription for this original transaction id,
    whose Apple-signed transaction names one of our bundle ids and the
    product the client claimed. None also when the API is not
    provisioned or the call fails: this fails closed.
    """
    if getattr(body, "signed_transaction", None):
        return None
    claimed_product_id = getattr(body, "product_id", None)
    otid = identity.original_transaction_id
    if identity.verified or not is_plausible_transaction_id(otid):
        return None
    from app.services import app_store_server_api as assa
    try:
        state = await assa.get_subscription_state(otid)
    except Exception as e:
        logger.warning("verify-receipt: Apple lookup failed for an unsigned claim: %s", e)
        return None
    if not state:
        return None
    if not state.get("entitled"):
        return None
    if state.get("bundle_id") not in allowed_bundle_ids(settings):
        return None
    if not claimed_product_id or state.get("product_id") != claimed_product_id:
        return None
    apple_txn = state.get("transaction_id")
    env = state.get("environment")
    return TransactionIdentity(
        original_transaction_id=otid,
        transaction_id=(apple_txn if is_plausible_transaction_id(apple_txn)
                        else identity.transaction_id),
        environment=env if env in ("Production", "Sandbox") else None,
        product_id=state.get("product_id"),
        verified=True,
        money={k: state.get(k) for k in (
            "price_paid", "currency", "offer_type", "offer_discount_type",
            "auto_renew_status")},
        via_lookup=True,
    )
