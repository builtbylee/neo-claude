"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration is loaded from environment variables prefixed with SL_."""

    # Database
    database_url: str = ""
    supabase_url: str = ""
    supabase_key: str = ""

    # APIs
    companies_house_api_key: str = ""
    github_token: str = ""
    sec_user_agent: str = "StartupLens research@example.com"

    model_config = {"env_file": ".env", "env_prefix": "SL_"}


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
