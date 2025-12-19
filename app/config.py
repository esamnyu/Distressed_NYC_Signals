"""Configuration settings for the NYC Distress Signal API."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Settings
    app_name: str = "NYC Distress Signal API"
    app_version: str = "1.0.0"
    debug: bool = False

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000

    # NYC OpenData (311) Settings
    nyc_opendata_app_token: Optional[str] = None
    nyc_311_dataset_id: str = "erm2-nwe9"  # 311 Service Requests dataset
    nyc_311_lookback_days: int = 90

    # DOB BIS Settings
    dob_bis_base_url: str = "http://a810-bisweb.nyc.gov/bisweb"
    dob_scrape_timeout_ms: int = 30000
    dob_retry_count: int = 2

    # Browser Settings
    browser_headless: bool = True
    browser_args: list = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]

    # Cache Settings
    cache_ttl_seconds: int = 86400  # 24 hours
    cache_directory: str = ".cache"

    # Rate Limiting
    rate_limit_requests_per_second: float = 1.0

    # CORS Settings
    cors_origins: list = ["*"]  # Restrict in production, e.g., ["https://yourdomain.com"]

    # Security Settings
    trusted_proxies: list = []  # IPs of trusted reverse proxies (e.g., ["127.0.0.1", "10.0.0.0/8"])
    max_request_body_size: int = 1048576  # 1MB max request body

    # API Key Authentication
    api_key_header: str = "Authorization"
    api_keys: list = []  # List of valid API keys (format: "Bearer sk_...")
    require_api_key: bool = False  # Set True for production
    admin_master_key: Optional[str] = None  # Master key for admin endpoints

    # Scoring Weights
    score_vacate_order: int = 50
    score_stop_work_order: int = 30
    score_illegal_conversion_threshold: int = 2
    score_illegal_conversion_bonus: int = 15
    score_heat_water_per_complaint: int = 5
    score_max: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
