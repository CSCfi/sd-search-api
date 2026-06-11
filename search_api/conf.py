from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class DeploymentConfiguration(BaseSettings):
    """Deployment configuration."""

    DEPLOYMENT_TYPE: Literal["Bigpicture"] = Field(
        description="Deployment type. Determines which router and database are used."
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


def ai_config() -> AIConfiguration:
    """Get AI configuration."""
    return AIConfiguration()
