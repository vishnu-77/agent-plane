"""Runtime configuration for the control plane.

Backends are selectable so the same code runs against zero-setup local stores
(SQLite + in-memory) or the docker-compose profile (Postgres + Redis).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Runtime ---
    # "production" turns on fail-closed startup checks (no default secrets, etc.).
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    # Comma-separated allowed CORS origins (e.g. "https://app.example.com"). Empty = none.
    cors_origins: str = ""

    # --- Abuse protection ---
    # Reject request bodies larger than this many bytes (0 = no limit).
    max_request_bytes: int = 1_000_000
    # Per-client request cap per minute (keyed by IP / X-Forwarded-For). 0 = disabled.
    rate_limit_per_minute: int = 600
    # Trust X-Forwarded-For for the client IP (only enable behind a trusted proxy).
    trust_forwarded_for: bool = False

    # --- Identity ---
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"

    # Identity verification mode (plug-and-play; flip via env, no code change):
    #   "jwt_claims" -> trust HS256 token claims (dev / simple integrations)
    #   "delegation" -> verify an Ed25519-signed delegation; the scope it carries
    #                   (tools, clearance) is authoritative, not caller-asserted.
    identity_mode: Literal["jwt_claims", "delegation"] = "jwt_claims"
    # PEM text *or* a path to a .pem holding the issuer's Ed25519 public key.
    delegation_public_key: str | None = None
    delegation_issuer: str | None = None      # optional expected `iss`
    delegation_audience: str | None = None    # optional expected `aud`
    revoked_jtis: str = ""                     # comma-separated jti values (kill switch)
    revocation_file: str | None = None         # optional file, one revoked jti per line

    # Agent-to-agent (A2A) delegation: the Ed25519 *private* key the control plane
    # uses to mint scoped child credentials. Pairs with delegation_public_key.
    # Unset = A2A delegation disabled. PEM text or path to a .pem.
    delegation_signing_key: str | None = None
    max_delegation_ttl_seconds: int = 3600     # cap on minted child-credential lifetime

    # --- Audit tamper-evidence (HMAC over the hash-chained decision record) ---
    audit_signing_key: str = "dev-audit-key-change-me"

    # --- Usage metering (foundation for usage-based billing later) ---
    usage_metering: bool = True
    # Optional YAML price book; unset -> config/pricing.yaml if present. Metering
    # runs regardless; pricing only adds an estimated cost to /v1/usage.
    pricing_file: str | None = None

    @property
    def delegation_public_key_pem(self) -> str | None:
        """Resolve the configured key to PEM text (inline value or file path)."""
        value = self.delegation_public_key
        if not value:
            return None
        path = Path(value)
        return path.read_text(encoding="utf-8") if path.exists() else value

    @property
    def delegation_signing_key_pem(self) -> str | None:
        value = self.delegation_signing_key
        if not value:
            return None
        path = Path(value)
        return path.read_text(encoding="utf-8") if path.exists() else value

    @property
    def revoked_jti_set(self) -> set[str]:
        """Revoked delegation ids, from the env list and/or a revocation file."""
        revoked = {j.strip() for j in self.revoked_jtis.split(",") if j.strip()}
        if self.revocation_file:
            path = Path(self.revocation_file)
            if path.exists():
                revoked |= {
                    ln.strip()
                    for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                }
        return revoked

    # --- Storage backend selection ---
    # "local"   -> SQLite audit + in-memory cache/quota (default, zero setup)
    # "postgres"-> Postgres audit + Redis cache/quota (docker-compose profile)
    storage_backend: Literal["local", "postgres"] = "local"

    sqlite_path: str = "audit.db"
    postgres_url: str = "postgresql+psycopg://agentplane:agentplane@localhost:5432/agentplane"
    redis_url: str = "redis://localhost:6379/0"

    # --- Policy ---
    policy_dir: str = "policies"

    # --- Model registry (config-driven; onboard models without code) ---
    # Path to a YAML model catalog. If unset, falls back to config/models.yaml
    # when present, else the built-in defaults.
    models_file: str | None = None

    # --- Tool registry (config-driven; the agent->tool broker edge) ---
    # YAML catalog of tools the broker may execute. Unset -> config/tools.yaml
    # if present, else no tools (default-deny). Agents never hold tool creds.
    tools_file: str | None = None

    # --- Knowledge sources (config-driven; the RAG authorization edge) ---
    # YAML catalog of retrieval sources + documents with access metadata.
    # Unset -> config/knowledge.yaml if present, else no sources (default-deny).
    knowledge_file: str | None = None

    # --- Authority leases (config-driven; the task-authority edge) ---
    # YAML catalog of task-bound AuthorityLease grants. Unset -> config/leases.yaml
    # if present, else no leases (default-deny: no lease means no authority).
    leases_file: str | None = None

    # --- Admin API (live revocation + policy hot-reload) ---
    # Unset = admin API disabled. Set a strong token to enable; callers pass it
    # in the X-Admin-Token header.
    admin_token: str | None = None

    # --- Provider credentials (real providers only) ---
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None  # e.g. https://my-res.openai.azure.com
    azure_openai_api_version: str = "2024-06-01"

    # --- Quotas (tokens per rolling window) ---
    default_token_quota: int = 100_000
    quota_window_seconds: int = 3600

    # --- Upstream call behaviour ---
    upstream_timeout_seconds: float = 60.0

    @property
    def audit_db_url(self) -> str:
        if self.storage_backend == "postgres":
            return self.postgres_url
        return f"sqlite:///{self.sqlite_path}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    _DEFAULT_SECRETS = {
        "jwt_secret": "dev-secret-change-me",
        "audit_signing_key": "dev-audit-key-change-me",
    }

    def production_errors(self) -> list[str]:
        """Fail-closed checks for ``environment=production`` (empty when OK)."""
        if self.environment != "production":
            return []
        errors: list[str] = []
        for field, default in self._DEFAULT_SECRETS.items():
            if getattr(self, field) == default:
                errors.append(f"{field.upper()} is still the insecure default")
        if self.identity_mode == "delegation" and not self.delegation_public_key:
            errors.append("IDENTITY_MODE=delegation but DELEGATION_PUBLIC_KEY is unset")
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()
