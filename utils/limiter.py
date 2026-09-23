from slowapi import Limiter
from slowapi.util import get_remote_address
from config import settings


def _storage_uri() -> str:
    """Redis es opcional; memoria alcanza para el lanzamiento inicial."""
    if settings.RATE_LIMIT_STORAGE_URI:
        return settings.RATE_LIMIT_STORAGE_URI.get_secret_value()
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/hour"],
    storage_uri=_storage_uri(),
)
