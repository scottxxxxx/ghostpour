from pydantic import BaseModel


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
    project_chat_used_this_period: int = 0
    project_chat_period: str | None = None  # "YYYY-MM" UTC; null until first send

    @property
    def effective_tier(self) -> str:
        """Return simulated_tier if active, otherwise real tier."""
        return self.simulated_tier or self.tier


class UserPublic(BaseModel):
    id: str
    tier: str
    email: str | None = None


class AppleAuthRequest(BaseModel):
    identity_token: str
    full_name: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
