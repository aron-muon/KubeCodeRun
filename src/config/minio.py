"""MinIO/S3 configuration."""

from typing import TYPE_CHECKING

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from minio import Minio


class MinIOConfig(BaseSettings):
    """MinIO/S3 storage settings."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
    )

    endpoint: str = Field(default="localhost:9000", alias="minio_endpoint")
    access_key: str | None = Field(
        default=None,
        alias="minio_access_key",
        validation_alias=AliasChoices("minio_access_key", "aws_access_key_id"),
    )
    secret_key: str | None = Field(
        default=None,
        alias="minio_secret_key",
        validation_alias=AliasChoices("minio_secret_key", "aws_secret_access_key"),
    )
    secure: bool = Field(default=False, alias="minio_secure")
    bucket: str = Field(default="kubecoderun-files", alias="minio_bucket")
    region: str = Field(default="us-east-1", alias="minio_region")
    use_iam: bool = Field(default=False, alias="minio_use_iam")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """Ensure endpoint doesn't include protocol."""
        if v.startswith(("http://", "https://")):
            raise ValueError("MinIO endpoint should not include protocol (use secure instead)")
        return v

    @model_validator(mode="after")
    def validate_credentials(self) -> "MinIOConfig":
        """Ensure credentials are provided when not using IAM."""
        if not self.use_iam:
            if not self.access_key or not self.secret_key:
                raise ValueError("MinIO access_key and secret_key are required when use_iam is False")
            if len(self.access_key) < 3:
                raise ValueError("MinIO access_key must be at least 3 characters")
            if len(self.secret_key) < 8:
                raise ValueError("MinIO secret_key must be at least 8 characters")

        return self

    def create_client(self) -> "Minio":
        """Create a MinIO client with the appropriate credentials.

        Uses IAM credentials provider when use_iam is True,
        otherwise uses access_key/secret_key.
        """
        import os

        from minio import Minio

        if self.use_iam:
            # Check if running with IRSA (web identity token)
            web_identity_token_file = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
            role_arn = os.environ.get("AWS_ROLE_ARN")

            if web_identity_token_file and role_arn:
                # Use WebIdentityProvider for IRSA (EKS service accounts)
                from minio.credentials import WebIdentityProvider

                def get_jwt() -> dict:
                    """Return JWT token as dict with access_token key."""
                    with open(web_identity_token_file) as f:
                        token = f.read().strip()
                    return {"access_token": token}

                # China regions use .amazonaws.com.cn domain
                if self.region.startswith("cn-"):
                    sts_endpoint = f"https://sts.{self.region}.amazonaws.com.cn"
                else:
                    sts_endpoint = f"https://sts.{self.region}.amazonaws.com"

                return Minio(
                    self.endpoint,
                    credentials=WebIdentityProvider(
                        jwt_provider_func=get_jwt,
                        sts_endpoint=sts_endpoint,
                        role_arn=role_arn,
                    ),
                    secure=self.secure,
                    region=self.region,
                )
            else:
                # Fall back to IamAwsProvider for EC2 instance profiles
                from minio.credentials import IamAwsProvider

                return Minio(
                    self.endpoint,
                    credentials=IamAwsProvider(),
                    secure=self.secure,
                    region=self.region,
                )
        else:
            # Use static credentials
            return Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
                region=self.region,
            )
