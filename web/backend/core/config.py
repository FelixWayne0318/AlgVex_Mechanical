"""
AlgVex Web Configuration
"""
import os
import warnings
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings
from typing import Optional


def _detect_algvex_path() -> str:
    """Auto-detect AlgVex project root path"""
    # 1. Environment variable takes priority
    env_path = os.getenv("ALGVEX_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    # 2. Detect relative to this file (web/backend/core/config.py -> project root)
    this_file = Path(__file__).resolve()
    project_root = this_file.parent.parent.parent.parent  # core -> backend -> web -> AlgVex
    if (project_root / "main_live.py").exists():
        return str(project_root)

    # 3. Common server path
    server_path = Path("/home/linuxuser/nautilus_AlgVex")
    if server_path.exists():
        return str(server_path)

    # 4. Fallback
    return str(project_root)


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "AlgVex"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-in-production-use-secrets")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "https://algvex.com/api/auth/callback/google"

    # Admin emails allowed to login
    ADMIN_EMAILS: list[str] = []

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./algvex.db"

    # AlgVex paths (auto-detected via validator - runs AFTER env load)
    ALGVEX_PATH: Optional[str] = None

    @field_validator('ALGVEX_PATH', mode='before')
    @classmethod
    def set_algvex_path(cls, v):
        if v is None:
            return _detect_algvex_path()
        return v

    @property
    def algvex_config_path(self) -> Path:
        """Derive config path from ALGVEX_PATH"""
        config_path = Path(self.ALGVEX_PATH) / "configs" / "base.yaml"
        if not config_path.exists():
            warnings.warn(f"⚠️  Config file not found: {config_path}. Backend may fail to read trader config.")
        return config_path

    ALGVEX_ENV_PATH: Path = Path.home() / ".env.algvex"
    ALGVEX_SERVICE_NAME: str = "nautilus-trader"

    # Binance API (read from AlgVex env)
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None

    # CORS
    CORS_ORIGINS: list[str] = [
        "https://algvex.com",
        "http://algvex.com",
        "https://www.algvex.com",
        "http://www.algvex.com",
        "http://139.180.157.152:3000",
        "http://139.180.157.152",
        "https://139.180.157.152:3000",
        "https://139.180.157.152",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# Validate SECRET_KEY: only crash if .env file exists (production) but SECRET_KEY is default
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists() and not settings.DEBUG:
    if settings.SECRET_KEY == "change-this-in-production-use-secrets":
        raise ValueError(
            "SECURITY ERROR: Default SECRET_KEY detected in production!\n"
            "   Set a secure SECRET_KEY in your .env file:\n"
            "   SECRET_KEY=$(openssl rand -hex 32)"
        )
elif not _env_file.exists() and settings.SECRET_KEY == "change-this-in-production-use-secrets":
    # No .env file = development/first-run mode, just warn
    warnings.warn(
        "Using default SECRET_KEY. Create web/backend/.env with a secure SECRET_KEY for production.",
        stacklevel=2,
    )


def load_algvex_env():
    """Load Binance API keys from AlgVex environment file"""
    env_path = settings.ALGVEX_ENV_PATH
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Strip inline comments
                    if "#" in value and not value.startswith('"'):
                        value = value.split("#")[0].strip()
                    value = value.strip().strip('"').strip("'")

                    if key == "BINANCE_API_KEY":
                        settings.BINANCE_API_KEY = value
                    elif key == "BINANCE_API_SECRET":
                        settings.BINANCE_API_SECRET = value


def _parse_env_value(raw: str) -> str:
    """Parse a single env value: strip inline comments and quotes."""
    if "#" in raw and not raw.startswith('"'):
        raw = raw.split("#")[0].strip()
    return raw.strip().strip('"').strip("'")


def read_algvex_env() -> dict[str, str]:
    """Read all key-value pairs from ~/.env.algvex."""
    env_path = settings.ALGVEX_ENV_PATH
    result: dict[str, str] = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    result[key.strip()] = _parse_env_value(value)
    return result


def write_algvex_env(updates: dict[str, str]) -> bool:
    """Update specific keys in ~/.env.algvex, preserving comments and order.

    Keys that exist are updated in-place. New keys are appended.
    Keys with value None or empty string are removed.
    """
    env_path = settings.ALGVEX_ENV_PATH
    lines: list[str] = []
    updated_keys: set[str] = set()

    if env_path.exists():
        with open(env_path) as f:
            lines = f.readlines()

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                updated_keys.add(key)
                val = updates[key]
                if val:  # non-empty → update
                    new_lines.append(f"{key}={val}\n")
                # empty/None → skip (remove key)
                continue
        new_lines.append(line)

    # Append new keys not already in file
    for key, val in updates.items():
        if key not in updated_keys and val:
            new_lines.append(f"{key}={val}\n")

    try:
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        return True
    except OSError:
        return False
