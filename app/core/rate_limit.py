from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.celery_broker_url if settings.rate_limit_enabled else "memory://",
    enabled=settings.rate_limit_enabled,
)
