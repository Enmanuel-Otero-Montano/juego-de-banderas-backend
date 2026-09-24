from pydantic import BaseModel

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    # Identificador estable para asociar las compras a la cuenta autenticada.
    user_id: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenData(BaseModel):
    user_id: int | None = None
