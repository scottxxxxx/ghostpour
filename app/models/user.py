from pydantic import BaseModel


class UserRecord(BaseModel):
    id: str
    apple_sub: str
    email: str | None = None
    tier: str = "free"
    created_at: str
    updated_at: str
    is_active: bool = True
    monthly_cost_limit_usd: float | None = None
    monthly_used_usd: float = 0
    overage_balance_usd: float = 0
    allocation_resets_at: str | None = None


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
