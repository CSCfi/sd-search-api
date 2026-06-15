from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class DeploymentConfiguration(BaseSettings):
    """Deployment configuration."""

    DEPLOYMENT_TYPE: Literal["Bigpicture"] = Field(
        description="Deployment type. Determines which router and database are used."
    )
    DEPLOYMENT_ENV: Literal["prod", "test", "dev", "staging"] = Field(
        default="dev",
        description="Deployment environment.",
    )


class DatabaseConfiguration(BaseSettings):
    """PostgreSQL connection configuration."""

    POSTGRES_HOST: str = Field(description="PostgreSQL host.")
    POSTGRES_PORT: int = Field(default=5432, description="PostgreSQL port.")
    POSTGRES_DB: str = Field(description="PostgreSQL database name.")
    POSTGRES_USER: str = Field(description="PostgreSQL user.")
    POSTGRES_PASSWORD: str = Field(description="PostgreSQL password.")


class OpenSearchConfiguration(BaseSettings):
    """OpenSearch connection configuration."""

    OPENSEARCH_HOST: str = Field(description="OpenSearch host.")
    OPENSEARCH_PORT: int = Field(default=9200, description="OpenSearch port.")
    OPENSEARCH_USER: str = Field(description="OpenSearch user.")
    OPENSEARCH_PASSWORD: str = Field(description="OpenSearch password.")


class SnowstormConfiguration(BaseSettings):
    """Snowstorm SNOMED CT server configuration."""

    SNOWSTORM_URL: str = Field(description="Snowstorm SNOMED CT server base URL.")


class SnomedTermCacheConfiguration(BaseSettings):
    """SNOMED preferred term in-memory cache configuration."""

    SNOMED_TERM_CACHE_REFRESH_INTERVAL: int = Field(
        default=300,
        description=(
            "How often (in seconds) the in-memory SNOMED preferred term cache is "
            "reloaded from the database. Defaults to 300 (5 minutes)."
        ),
    )


class FeatureConfiguration(BaseSettings):
    """Feature flags."""

    FEATURE_AI: bool = Field(
        default=False,
        description="Enable the POST /ai/query endpoint. Disabled by default.",
    )


class AIConfiguration(BaseSettings):
    """AI/LLM configuration."""

    LLM_BASE_URL: str = Field(description="LLM API base URL.")
    LLM_API_KEY: str = Field(description="LLM API key.")


def deployment_config() -> DeploymentConfiguration:
    """Get deployment configuration."""
    return DeploymentConfiguration()


def database_config() -> DatabaseConfiguration:
    """Get database configuration."""
    return DatabaseConfiguration()


def opensearch_config() -> OpenSearchConfiguration:
    """Get OpenSearch configuration."""
    return OpenSearchConfiguration()


def snowstorm_config() -> SnowstormConfiguration:
    """Get Snowstorm configuration."""
    return SnowstormConfiguration()


def snomed_term_cache_config() -> SnomedTermCacheConfiguration:
    """Get SNOMED term cache configuration."""
    return SnomedTermCacheConfiguration()


def feature_config() -> FeatureConfiguration:
    """Get feature flag configuration."""
    return FeatureConfiguration()


def ai_config() -> AIConfiguration:
    """Get AI configuration."""
    return AIConfiguration()
