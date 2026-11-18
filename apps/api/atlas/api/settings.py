"""API settings read from environment variables. Secrets are never committed."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEV_DATA_KEY = "development-only-data-key-change-me"
DEV_PEPPER = "development-only-pepper-change-me"


@dataclass
class Settings:
    env: str = "development"
    database_url: str = "postgresql://atlas_app@127.0.0.1:54329/atlas"
    pool_size: int = 10
    valkey_url: str | None = None
    jwt_private_pem: str = ""
    jwt_public_pem: str = ""
    jwt_issuer: str = "atlas-api"
    jwt_audience: str = "atlas-web"
    data_key: str = DEV_DATA_KEY
    token_pepper: str = DEV_PEPPER
    cookie_secure: bool = True
    access_ttl_seconds: int = 900
    idle_ttl_seconds: int = 8 * 3600
    absolute_ttl_seconds: int = 7 * 86400
    reset_ttl_seconds: int = 3600
    mfa_token_ttl_seconds: int = 300
    max_body_bytes: int = 1_000_000
    max_batch_body_bytes: int = 5_000_000
    max_batch_items: int = 1000
    lockout_threshold: int = 5
    trust_proxy_headers: bool = False
    # Rate limits as (requests, window seconds).
    limit_ip: tuple[int, int] = (300, 60)
    limit_user: tuple[int, int] = (240, 60)
    limit_api_key: tuple[int, int] = (600, 60)
    limit_login_ip: tuple[int, int] = (20, 300)
    limit_login_account: tuple[int, int] = (10, 900)
    limit_public_form: tuple[int, int] = (5, 3600)
    limit_reset: tuple[int, int] = (5, 3600)
    argon_time_cost: int = 3
    argon_memory_kib: int = 65536
    argon_parallelism: int = 4
    smtp_host: str | None = None
    smtp_port: int = 1025
    mail_from: str = "no-reply@atlas.invalid"
    web_origin: str = "http://localhost:3000"
    vector_search: bool = False
    queue_sla_hours: int = 72
    cache_ttl_seconds: float = 60.0
    celery_broker_url: str | None = None
    worker_database_url: str = "postgresql://atlas_worker@127.0.0.1:54329/atlas"
    nel_url: str = "http://127.0.0.1:8082"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def production(self) -> bool:
        return self.env == "production"


def generate_keypair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


def public_from_private(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    assert isinstance(key, Ed25519PrivateKey)
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def from_env() -> Settings:
    env = os.environ.get("ATLAS_ENV", "development")
    settings = Settings(
        env=env,
        database_url=os.environ.get("ATLAS_APP_DATABASE_URL", Settings.database_url),
        valkey_url=os.environ.get("ATLAS_VALKEY_URL") or None,
        jwt_private_pem=os.environ.get("ATLAS_JWT_PRIVATE_KEY", ""),
        data_key=os.environ.get("ATLAS_DATA_KEY", DEV_DATA_KEY),
        token_pepper=os.environ.get("ATLAS_TOKEN_PEPPER", DEV_PEPPER),
        cookie_secure=os.environ.get("ATLAS_COOKIE_SECURE", "1") != "0",
        smtp_host=os.environ.get("ATLAS_SMTP_HOST") or None,
        smtp_port=int(os.environ.get("ATLAS_SMTP_PORT", "1025")),
        web_origin=os.environ.get("ATLAS_WEB_ORIGIN", "http://localhost:3000"),
        nel_url=os.environ.get("ATLAS_NEL_URL", "http://127.0.0.1:8082"),
        vector_search=os.environ.get("ATLAS_VECTOR_SEARCH", "0") == "1",
        celery_broker_url=os.environ.get("ATLAS_CELERY_BROKER_URL") or None,
        worker_database_url=os.environ.get(
            "ATLAS_WORKER_DATABASE_URL", Settings.worker_database_url
        ),
        trust_proxy_headers=os.environ.get("ATLAS_TRUST_PROXY", "0") == "1",
    )
    if settings.production and (
        not settings.jwt_private_pem
        or settings.data_key == DEV_DATA_KEY
        or settings.token_pepper == DEV_PEPPER
    ):
        raise RuntimeError("production requires ATLAS_JWT_PRIVATE_KEY, ATLAS_DATA_KEY and pepper")
    return finalize(settings)


def finalize(settings: Settings) -> Settings:
    """Fill the signing key pair. A development process without a key uses an ephemeral one."""
    if not settings.jwt_private_pem:
        settings.jwt_private_pem, settings.jwt_public_pem = generate_keypair()
    elif not settings.jwt_public_pem:
        settings.jwt_public_pem = public_from_private(settings.jwt_private_pem)
    return settings
