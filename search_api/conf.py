from pydantic import Field
from pydantic_settings import BaseSettings


class CommonConfiguration(BaseSettings):
    """Common configuration shared across the application."""

    POSTGRES_HOST: str = Field(
        default="localhost", description="PostgreSQL host."
    )  # TODO: remove default value
    POSTGRES_PORT: int = Field(default=5432, description="PostgreSQL port.")
    POSTGRES_DB: str = Field(
        default="sd_search", description="PostgreSQL database name."
    )  # TODO: remove default value
    POSTGRES_USER: str = Field(
        default="postgres", description="PostgreSQL user."
    )  # TODO: remove default value
    POSTGRES_PASSWORD: str = Field(
        default="test", description="PostgreSQL password."
    )  # TODO: remove default value
    OPENSEARCH_HOST: str = Field(
        default="host.docker.internal", description="OpenSearch host."
    )  # TODO: remove default value
    OPENSEARCH_PORT: int = Field(default=9200, description="OpenSearch port.")
    OPENSEARCH_USER: str = Field(
        default="admin", description="OpenSearch user."
    )  # TODO: remove default value
    OPENSEARCH_PASSWORD: str = Field(
        default="Sd@Search9x!", description="OpenSearch password."
    )  # TODO: remove default value
    SNOWSTORM_URL: str = Field(
        # default="https://snowstorm.ihtsdotools.org/snowstorm/snomed-ct",
        # default="http://snowstorm:8080/snowstorm/snomed-ct",
        default="",
        description="Snowstorm SNOMED CT server base URL.",
    )


class BigpictureConfiguration(BaseSettings):
    """Bigpicture beacon configuration."""

    LLM_BASE_URL: str = Field(
        default="http://localhost:11434/v1", description="LLM API base URL."
    )
    LLM_API_KEY: str = Field(
        default="ollama", description="LLM API key."
    )  # TODO: remove default value


def common_config() -> CommonConfiguration:
    """Get common configuration."""
    return CommonConfiguration()


def bigpicture_config() -> BigpictureConfiguration:
    """Get Bigpicture configuration."""
    return BigpictureConfiguration()
