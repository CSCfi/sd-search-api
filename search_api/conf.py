import base64
from typing import Literal
from urllib.parse import urljoin, urlparse

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings


class DeploymentConfiguration(BaseSettings):
    """Deployment configuration."""

    DEPLOYMENT_TYPE: str = Field(
        description="Deployment name. Must match a domain registered in the deployment "
        "registry. Determines which fields are indexed."
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

    POSTGRES_POOL_MIN_SIZE: int = Field(
        default=2, description="Connections the pool keeps open."
    )
    POSTGRES_POOL_MAX_SIZE: int = Field(
        default=10, description="Connections the pool may open at once."
    )
    POSTGRES_POOL_MAX_LIFETIME: float = Field(
        default=3600.0,
        description="Seconds after which a pooled connection is replaced.",
    )
    POSTGRES_POOL_TIMEOUT: float = Field(
        default=5.0,
        description=(
            "Seconds a caller waits for a connection from the pool before failing."
        ),
    )


class OpenSearchConfiguration(BaseSettings):
    """OpenSearch connection configuration."""

    OPENSEARCH_HOST: str = Field(description="OpenSearch host.")
    OPENSEARCH_PORT: int = Field(default=9200, description="OpenSearch port.")
    OPENSEARCH_USER: str = Field(description="OpenSearch user.")
    OPENSEARCH_PASSWORD: str = Field(description="OpenSearch password.")


class SnowstormConfiguration(BaseSettings):
    """Snowstorm SNOMED CT server configuration."""

    SNOWSTORM_URL: str = Field(description="Snowstorm SNOMED CT server base URL.")


class CacheConfiguration(BaseSettings):
    """In-memory cache configuration."""

    ONTOLOGY_CACHE_REFRESH: int = Field(
        default=300,
        description=(
            "Frequency (in seconds) to check whether the stored ontology changed."
        ),
    )
    TERM_CACHE_REFRESH: int = Field(
        default=300,
        description=(
            "Frequency (in seconds) to check whether an ontology's cached "
            "preferred terms changed."
        ),
    )
    VALUE_COUNT_CACHE_REFRESH: int = Field(
        default=300,
        description=(
            "Frequency (in seconds) to check whether a document has been synced to "
            "the search index since the value counts were last counted."
        ),
    )


class AdminConfiguration(BaseSettings):
    """Admin API configuration."""

    ADMIN_KEY: str | None = Field(
        default=None,
        description=(
            "Secret key required to use admin endpoints. "
            "Admin endpoints are not mounted when this is unset."
        ),
    )


class FeatureConfiguration(BaseSettings):
    """Feature flags."""

    FEATURE_AI: bool = Field(
        default=False,
        description="Enable the POST /ai/* endpoints. Disabled by default.",
    )


class AIConfiguration(BaseSettings):
    """AI/LLM configuration."""

    LLM_BASE_URL: str = Field(description="LLM API base URL.")
    LLM_API_KEY: str = Field(description="LLM API key.")


class OIDCConfiguration(BaseSettings):
    """OIDC relying party configuration."""

    BASE_URL: str = Field(description="Public base URL this API is served at.")
    OIDC_URL: str = Field(description=("OIDC issuer URL."))
    OIDC_CLIENT_ID: str = Field(
        description="OIDC client ID registered with the issuer."
    )
    OIDC_CLIENT_SECRET: str = Field(
        description="OIDC client secret registered with the issuer."
    )
    OIDC_REDIRECT_URL: str | None = Field(
        default=None,
        description=(
            "OIDC redirect URL to send the user to after login/logout. Defaults to "
            "/docs endpoint when unset."
        ),
    )
    OIDC_SCOPE: str = Field(
        default="openid profile email", description="OIDC scopes to request."
    )
    OIDC_SECURE_COOKIE: bool = Field(
        default=True,
        description=(
            "Set the Secure attribute on session/login cookies. Disable only for "
            "plain-HTTP deployments (e.g. local development without TLS)."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def callback_url(self) -> str:
        """OIDC callback URL registered with the issuer."""
        return urljoin(self.BASE_URL, "/callback")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redirect_url(self) -> str:
        """URL to send the user to after login/logout."""
        return self.OIDC_REDIRECT_URL or urljoin(self.BASE_URL, "/docs")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def post_logout_redirect_url(self) -> str:
        """URL to send the user to after logout: redirect_url's origin, path dropped."""
        parsed = urlparse(self.redirect_url)
        return f"{parsed.scheme}://{parsed.netloc}/"


class JWTConfiguration(BaseSettings):
    """Session JWT configuration."""

    JWT_KEY: str = Field(
        description="Base64-encoded secret key used to sign session JWTs."
    )
    JWT_ISSUER: str = Field(
        default="sd-search-api", description="Session JWT issuer claim."
    )
    JWT_ALGORITHM: str = Field(
        default="HS256", description="Session JWT signing algorithm."
    )

    @field_validator("JWT_KEY")
    @classmethod
    def decode_jwt_key(cls, value: str) -> str:
        """Decode JWT key from base64-encoded environment variable."""
        try:
            decoded = base64.b64decode(value, validate=True)
            key = decoded.decode("utf-8")
        except Exception as exc:
            raise ValueError("JWT_KEY must be a valid base64-encoded string") from exc
        if len(decoded) < 32:
            raise ValueError(
                "JWT_KEY must decode to at least 32 bytes (256 bits) for HS256 signing"
            )
        return key


class SdSubmitSyncConfiguration(BaseSettings):
    """Configuration for syncing published submissions from the SD Submit API."""

    SD_SUBMIT_API_URL: str = Field(description="SD Submit API base URL.")
    SD_SUBMIT_PRIVATE_KEY: str = Field(
        description=(
            "Base64-encoded PEM EC private key to sign the SD Submit API JWT token. "
            "The submit API has the public key to verify the JWT token."
        )
    )
    SD_SUBMIT_AUDIENCE: str = Field(
        description=(
            "Audience of the SD Submit API JWT tokens. Must be the audience "
            "the API expects, or the JWT token is rejected."
        )
    )
    SD_SUBMIT_ISSUER: str = Field(
        default="sd-search-api",
        description="Issuer of the SD Submit API tokens, naming this service.",
    )

    @field_validator("SD_SUBMIT_PRIVATE_KEY")
    @classmethod
    def decode_private_key(cls, value: str) -> str:
        """Decode and parse the base64-encoded PEM private key."""
        try:
            key = base64.b64decode(value, validate=True)
        except Exception as ex:
            raise ValueError(
                "SD_SUBMIT_PRIVATE_KEY must be a valid base64-encoded string"
            ) from ex
        # Parse the key here to report errors when the configuration is loaded.
        try:
            load_pem_private_key(key, password=None)
        except Exception as ex:
            raise ValueError(
                "SD_SUBMIT_PRIVATE_KEY must decode to an unencrypted PEM private key"
            ) from ex
        return key.decode("utf-8")


class Crypt4GHConfiguration(BaseSettings):
    """Configuration for decrypting Crypt4GH encrypted source material."""

    C4GH_KEY_FILE: str | None = Field(
        default=None,
        description=(
            "Path to a Crypt4GH private key file (.sec) for decrypting .c4gh files. "
            "Unset when the source material is not encrypted."
        ),
    )
    C4GH_PASSPHRASE: str | None = Field(
        default=None,
        description="Passphrase of the Crypt4GH private key, unset for an unprotected key.",
    )


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


def admin_config() -> AdminConfiguration:
    """Get admin configuration."""
    return AdminConfiguration()


def cache_config() -> CacheConfiguration:
    """Get in-memory cache configuration."""
    return CacheConfiguration()


def feature_config() -> FeatureConfiguration:
    """Get feature flag configuration."""
    return FeatureConfiguration()


def ai_config() -> AIConfiguration:
    """Get AI configuration."""
    return AIConfiguration()


def oidc_config() -> OIDCConfiguration:
    """Get OIDC configuration."""
    return OIDCConfiguration()


def jwt_config() -> JWTConfiguration:
    """Get JWT configuration."""
    return JWTConfiguration()


def sd_submit_sync_config() -> SdSubmitSyncConfiguration:
    """Get the SD Submit API sync configuration."""
    return SdSubmitSyncConfiguration()


def crypt4gh_config() -> Crypt4GHConfiguration:
    """Get the Crypt4GH decryption configuration."""
    return Crypt4GHConfiguration()
