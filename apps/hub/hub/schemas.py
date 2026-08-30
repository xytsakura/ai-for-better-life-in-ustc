from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


class LinkIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["link"]
    launch_url: HttpUrl


class ConnectedIntegration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["connected"]
    protocol: Literal["ag-ui", "simple-chat"]
    launch_url: HttpUrl
    chat_endpoint: HttpUrl
    health_endpoint: HttpUrl
    callback_urls: list[HttpUrl] = Field(default_factory=list, max_length=8)

    @field_validator("callback_urls")
    @classmethod
    def unique_callback_urls(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        if len({str(item) for item in value}) != len(value):
            raise ValueError("callback_urls must be unique")
        return value


Integration = Annotated[
    Union[LinkIntegration, ConnectedIntegration],
    Field(discriminator="mode"),
]


class DataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receives_user_identity: bool
    receives_files: bool
    stores_conversation: bool
    privacy_url: HttpUrl | None = None


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    )
    owner: str = Field(min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=254)
    category: str = Field(min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=12)
    icon: str | None = Field(default=None, max_length=2048)
    integration: Integration
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    model_runtime: "ModelRuntime | None" = None
    data_policy: DataPolicy

    @field_validator("contact")
    @classmethod
    def valid_contact_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("contact must be an email address")
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("tags must be unique")
        if any(item != item.strip() or not 1 <= len(item) <= 32 for item in value):
            raise ValueError("tags must contain 1-32 character strings without surrounding whitespace")
        return value

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("capabilities must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", item) for item in value):
            raise ValueError("capabilities must use lowercase kebab-case identifiers")
        return value


class AgentSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: AgentManifest
    trust_level: Literal["third_party_external", "first_party_internal"] = "third_party_external"


class ModelRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["agent_managed", "platform_optional", "platform_required"] = "agent_managed"
    gateway_contract: Literal["campus-model-gateway-v1"] | None = None
    supported_api_styles: list[Literal["responses", "chat_completions"]] = Field(
        default_factory=list,
        max_length=4,
    )

    @field_validator("supported_api_styles")
    @classmethod
    def unique_supported_api_styles(
        cls, value: list[Literal["responses", "chat_completions"]]
    ) -> list[Literal["responses", "chat_completions"]]:
        if len(set(value)) != len(value):
            raise ValueError("supported_api_styles must be unique")
        return value


ManifestAdapter = TypeAdapter(AgentManifest)


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str = Field(min_length=1, max_length=1000)
    featured: bool = False


class StatusChangeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class RollbackRequest(BaseModel):
    version_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)


class CredentialStatusRequest(BaseModel):
    status: Literal["rotating", "revoked"]
    reason: str = Field(min_length=1, max_length=1000)


class WorkspaceStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=16, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    scope: str


class RunAgentInput(BaseModel):
    threadId: str
    runId: str
    parentRunId: str | None = None
    state: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    context: list[dict[str, Any]] = Field(default_factory=list)
    forwardedProps: dict[str, Any] | None = None

    model_config = {"extra": "allow"}
