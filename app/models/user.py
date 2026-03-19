from pydantic import BaseModel


class UserRecord(BaseModel):
    id: str
    apple_sub: str
    email: str | None = None
    tier: str = "free"
    created_at: str
    updated_at: str
    is_active: bool = True


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
