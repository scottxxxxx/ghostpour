from pydantic import BaseModel


# An anonymous purchase account (2026-09-26, Apple's third 5.1.1(v) rejection
# of iOS 1.18: a purchase may not require registration). It has no Apple
# identity, so `apple_sub` carries this prefix plus the sha256 of the
# device's install id: `users.apple_sub` is UNIQUE NOT NULL, and the hash
# makes creation idempotent per install without storing the credential.
ANONYMOUS_SUB_PREFIX = "anonymous:"


class UserRecord(BaseModel):
    id: str
    apple_sub: str
    email: str | None = None
    display_name: str | None = None
    tier: str = "free"
    created_at: str
    updated_at: str
    is_active: bool = True
    monthly_cost_limit_usd: float | None = None
    monthly_used_usd: float = 0
    overage_balance_usd: float = 0
    allocation_resets_at: str | None = None
    simulated_tier: str | None = None
    simulated_exhausted: bool = False
    is_trial: bool = False
    trial_start: str | None = None
    trial_end: str | None = None
    ever_subscribed: bool = False
    first_subscribed_at: str | None = None
    memory_used_this_period: int = 0
    memory_period: str | None = None  # "YYYY-MM" UTC; null until first capture
    memory_last_origin_id: str | None = None  # set at capture-transcript time
    memory_last_cta_kind: str | None = None   # consumed + cleared by next quilt fetch

    @property
    def is_anonymous(self) -> bool:
        return self.apple_sub.startswith(ANONYMOUS_SUB_PREFIX)

    @property
    def effective_tier(self) -> str:
        """Return simulated_tier if active, otherwise real tier."""
        return self.simulated_tier or self.tier


class UserPublic(BaseModel):
    id: str
    tier: str
    email: str | None = None
    # Apple hands fullName over ONCE, on the very first authorization; GP
    # persists it then and returns it on EVERY sign-in and refresh so a
    # fresh phone can rehydrate the display name (2026-08-24 lost-phone
    # mandate). A later null never overwrites a stored name.
    display_name: str | None = None
    # True for an anonymous purchase account (no Apple identity yet).
    is_anonymous: bool = False


class AppleAuthRequest(BaseModel):
    identity_token: str
    full_name: str | None = None
    # The anonymous purchase account's ACCESS token, when a signed-out buyer
    # signs in. Refresh it first: an expired one merges nothing and says so.
    anonymous_token: str | None = None


class AnonymousAuthRequest(BaseModel):
    # A UUID the client keeps in the Keychain. It is a CREDENTIAL: whoever
    # holds it can mint tokens for the account and its plan. GP stores only
    # its sha256 and never logs it.
    install_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
    # /auth/apple only, what happened to an `anonymous_token` (check the echo,
    # rule 4): "converted" (a new Apple ID took over the anonymous account),
    # "merged" (it moved into an existing Apple account and closed), or
    # "none". `merge_reason` says why a token merged nothing; `plan_conflict`
    # is true when both accounts were already paid and each kept its own.
    merge: str | None = None
    merge_reason: str | None = None
    plan_conflict: bool | None = None
