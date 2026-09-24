from __future__ import annotations

import ssl
from functools import lru_cache

import truststore


@lru_cache(maxsize=1)
def system_tls_context() -> ssl.SSLContext:
    """Возвращает TLS-контекст с доверенными сертификатами операционной системы."""

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
