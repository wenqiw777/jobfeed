"""Provider authentication and model discovery for onboarding."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass

import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.onboarding_types import (
    API_PROVIDERS,
    ConnectionResult,
    ProviderModel,
    ProviderName,
)


@dataclass(frozen=True, kw_only=True)
class ProcessResult:
    """Captured output from a provider CLI status command."""

    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[Sequence[str]], Awaitable[ProcessResult]]
ExecutableLookup = Callable[[str], str | None]
BedrockSessionFactory = Callable[[str | None], object]
_PROCESS_TIMEOUT_SECONDS = 15.0
_BEDROCK_RECOMMENDED = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": 0,
    "us.anthropic.claude-sonnet-5": 1,
}


class ProviderChecker:
    """Check official APIs or signed-in local CLIs without inference calls."""

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        process_runner: ProcessRunner | None = None,
        executable_lookup: ExecutableLookup = shutil.which,
        bedrock_session_factory: BedrockSessionFactory | None = None,
    ) -> None:
        """Create a checker with optionally injected HTTP/process boundaries."""
        self._http_client = http_client
        self._run_process = process_runner or _run_process
        self._find_executable = executable_lookup
        self._bedrock_session_factory = bedrock_session_factory or _bedrock_session
        self._bedrock_priced_models = frozenset(load_price_table())

    async def check(
        self,
        provider: ProviderName,
        *,
        api_key: str | None = None,
        region: str | None = None,
        profile: str | None = None,
    ) -> ConnectionResult:
        """Verify one provider and return a redacted available-model catalog.

        Args:
            provider: Official API or local CLI provider to verify.
            api_key: Credential for an API provider; unused for CLI providers.

        Returns:
            Redacted connection result and provider-specific models.
        """
        if provider in API_PROVIDERS and not api_key:
            return _failure(provider, "Enter an API key, then try again.")
        if provider == "openai_api":
            return await self._check_openai(api_key or "")
        if provider == "anthropic_api":
            return await self._check_anthropic(api_key or "")
        if provider == "codex_cli":
            return await self._check_codex()
        if provider == "amazon_bedrock":
            return await self._check_bedrock(region=region, profile=profile)
        return await self._check_claude()

    async def _check_bedrock(
        self, *, region: str | None, profile: str | None
    ) -> ConnectionResult:
        if not region or not region.strip():
            return _failure("amazon_bedrock", "Choose an AWS Region, then try again.")
        normalized_region = region.strip()
        normalized_profile = profile.strip() if profile and profile.strip() else None
        try:
            models = await asyncio.to_thread(
                self._discover_bedrock_models,
                normalized_region,
                normalized_profile,
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", "")) if isinstance(error, dict) else ""
            detail = (
                "AWS credentials were found, but Bedrock model discovery was "
                "denied. Check the profile IAM permissions."
                if code in {"AccessDeniedException", "UnauthorizedException"}
                else f"Amazon Bedrock returned {code or 'an AWS error'}. Try again."
            )
            return _bedrock_failure(detail, normalized_region, normalized_profile)
        except BotoCoreError:
            return _bedrock_failure(
                "AWS credentials or profile could not be loaded. Sign in and retry.",
                normalized_region,
                normalized_profile,
            )
        if not models:
            return _bedrock_failure(
                "Connected, but no priced Converse models were found in this Region.",
                normalized_region,
                normalized_profile,
            )
        return ConnectionResult(
            provider="amazon_bedrock",
            connected=True,
            detail="Amazon Bedrock credentials and model catalog verified.",
            models=models,
            region=normalized_region,
            profile=normalized_profile,
        )

    def _discover_bedrock_models(
        self, region: str, profile: str | None
    ) -> tuple[ProviderModel, ...]:
        session = self._bedrock_session_factory(profile)
        client = session.client(  # type: ignore[attr-defined]
            "bedrock",
            region_name=region,
            config=Config(
                read_timeout=_PROCESS_TIMEOUT_SECONDS,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        foundations = client.list_foundation_models(
            byOutputModality="TEXT",
            byInferenceType="ON_DEMAND",
        )
        models = _bedrock_foundation_models(foundations, self._bedrock_priced_models)
        profiles: list[dict[str, object]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"maxResults": 100}
            if token is not None:
                kwargs["nextToken"] = token
            page = client.list_inference_profiles(**kwargs)
            rows = page.get("inferenceProfileSummaries", [])
            if isinstance(rows, list):
                profiles.extend(row for row in rows if isinstance(row, dict))
            next_token = page.get("nextToken")
            token = next_token if isinstance(next_token, str) and next_token else None
            if token is None:
                break
        models.extend(
            _bedrock_inference_profiles(profiles, self._bedrock_priced_models)
        )
        models.sort(
            key=lambda model: (
                _BEDROCK_RECOMMENDED.get(model.id, 2),
                model.label.casefold(),
                model.id,
            )
        )
        return tuple(models)

    async def _check_openai(self, api_key: str) -> ConnectionResult:
        headers = {"Authorization": f"Bearer {api_key}"}
        return await self._check_api(
            "openai_api",
            "https://api.openai.com/v1/models",
            headers,
            _openai_models,
        )

    async def _check_anthropic(self, api_key: str) -> ConnectionResult:
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        return await self._check_api(
            "anthropic_api",
            "https://api.anthropic.com/v1/models?limit=1000",
            headers,
            _anthropic_models,
        )

    async def _check_api(
        self,
        provider: ProviderName,
        url: str,
        headers: dict[str, str],
        parser: Callable[[object], tuple[ProviderModel, ...]],
    ) -> ConnectionResult:
        try:
            payload = await self._get_json(url, headers)
            models = parser(payload)
            if not models:
                return _failure(
                    provider, "Connected, but no evaluation models were found."
                )
            return ConnectionResult(
                provider=provider,
                connected=True,
                detail=f"{_provider_label(provider)} connection verified.",
                models=models,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                return _failure(
                    provider, "The API key was rejected. Check it and retry."
                )
            return _failure(
                provider,
                f"The provider returned HTTP {exc.response.status_code}. Try again.",
            )
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return _failure(provider, "The provider could not be reached. Try again.")

    async def _get_json(self, url: str, headers: dict[str, str]) -> object:
        if self._http_client is not None:
            response = await self._http_client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _check_codex(self) -> ConnectionResult:
        missing = self._missing_cli("codex_cli", "codex")
        if missing is not None:
            return missing
        status = await self._run_process(["codex", "login", "status"])
        if status.returncode != 0:
            return _failure("codex_cli", "Codex is installed but not signed in.")
        catalog = await self._run_process(["codex", "debug", "models"])
        if catalog.returncode != 0:
            return _failure("codex_cli", "Codex models could not be loaded. Try again.")
        try:
            models = _codex_models(json.loads(catalog.stdout))
        except (json.JSONDecodeError, TypeError, ValueError):
            return _failure("codex_cli", "Codex returned an unreadable model list.")
        return ConnectionResult(
            provider="codex_cli",
            connected=bool(models),
            detail=(
                "Codex login verified." if models else "Codex has no available models."
            ),
            models=models,
        )

    async def _check_claude(self) -> ConnectionResult:
        missing = self._missing_cli("claude_cli", "claude")
        if missing is not None:
            return missing
        status = await self._run_process(["claude", "auth", "status"])
        try:
            payload = json.loads(status.stdout)
        except json.JSONDecodeError:
            payload = {}
        if status.returncode != 0 or not payload.get("loggedIn"):
            return _failure("claude_cli", "Claude Code is installed but not signed in.")
        models = tuple(
            ProviderModel(id=model, label=f"Claude {model.title()} (latest)")
            for model in ("haiku", "sonnet", "opus")
        )
        return ConnectionResult(
            provider="claude_cli",
            connected=True,
            detail="Claude Code login verified.",
            models=models,
        )

    def _missing_cli(
        self, provider: ProviderName, executable: str
    ) -> ConnectionResult | None:
        if self._find_executable(executable) is not None:
            return None
        return _failure(provider, f"{executable} is not installed or not on PATH.")


def _openai_models(payload: object) -> tuple[ProviderModel, ...]:
    data = _model_rows(payload)
    excluded = ("audio", "realtime", "transcribe", "tts", "image", "search")
    ids: list[str] = []
    for row in data:
        model_id = row.get("id")
        if not isinstance(model_id, str):
            continue
        if not model_id.startswith(("gpt-", "o1", "o3", "o4")):
            continue
        if not any(part in model_id for part in excluded):
            ids.append(model_id)
    return tuple(ProviderModel(id=model, label=model) for model in sorted(set(ids)))


def _anthropic_models(payload: object) -> tuple[ProviderModel, ...]:
    models = []
    for row in _model_rows(payload):
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id.startswith("claude-"):
            continue
        label = row.get("display_name")
        models.append(
            ProviderModel(
                id=model_id,
                label=label if isinstance(label, str) else model_id,
            )
        )
    return tuple(models)


def _codex_models(payload: object) -> tuple[ProviderModel, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("invalid Codex model catalog")
    models = []
    for row in payload["models"]:
        if not isinstance(row, dict) or row.get("visibility") != "list":
            continue
        model_id = row.get("slug")
        label = row.get("display_name")
        if isinstance(model_id, str):
            models.append(
                ProviderModel(
                    id=model_id,
                    label=label if isinstance(label, str) else model_id,
                )
            )
    return tuple(models)


def _model_rows(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid model response")
    return [row for row in payload["data"] if isinstance(row, dict)]


def _failure(provider: ProviderName, detail: str) -> ConnectionResult:
    return ConnectionResult(provider=provider, connected=False, detail=detail)


def _provider_label(provider: ProviderName) -> str:
    return "OpenAI API" if provider == "openai_api" else "Anthropic API"


def _bedrock_foundation_models(
    payload: object, priced_models: frozenset[str]
) -> list[ProviderModel]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("modelSummaries"), list
    ):
        raise ValueError("invalid Bedrock foundation model response")
    models: list[ProviderModel] = []
    for row in payload["modelSummaries"]:
        if not isinstance(row, dict):
            continue
        model_id = row.get("modelId")
        lifecycle = row.get("modelLifecycle")
        outputs = row.get("outputModalities")
        if (
            not isinstance(model_id, str)
            or model_id not in priced_models
            or not isinstance(lifecycle, dict)
            or lifecycle.get("status") != "ACTIVE"
            or not isinstance(outputs, list)
            or "TEXT" not in outputs
        ):
            continue
        name = row.get("modelName")
        label = name if isinstance(name, str) and name else model_id
        models.append(
            ProviderModel(
                id=model_id,
                label=f"{label} · Foundation model",
                kind="foundation_model",
                pricing_model=model_id,
            )
        )
    return models


def _bedrock_inference_profiles(
    rows: list[dict[str, object]], priced_models: frozenset[str]
) -> list[ProviderModel]:
    models: list[ProviderModel] = []
    for row in rows:
        if row.get("status") != "ACTIVE" or row.get("type") != "SYSTEM_DEFINED":
            continue
        identifier = row.get("inferenceProfileId")
        pricing_model = _profile_pricing_model(row)
        if (
            not isinstance(identifier, str)
            or pricing_model is None
            or (identifier not in priced_models and pricing_model not in priced_models)
        ):
            continue
        name = row.get("inferenceProfileName")
        label = name if isinstance(name, str) and name else identifier
        models.append(
            ProviderModel(
                id=identifier,
                label=f"{label} · Inference profile",
                kind="inference_profile",
                pricing_model=pricing_model,
            )
        )
    return models


def _profile_pricing_model(row: dict[str, object]) -> str | None:
    models = row.get("models")
    if not isinstance(models, list) or not models:
        return None
    first = models[0]
    arn = first.get("modelArn") if isinstance(first, dict) else None
    if not isinstance(arn, str) or "/" not in arn:
        return None
    return arn.rsplit("/", 1)[-1]


def _bedrock_failure(detail: str, region: str, profile: str | None) -> ConnectionResult:
    return ConnectionResult(
        provider="amazon_bedrock",
        connected=False,
        detail=detail,
        region=region,
        profile=profile,
    )


def _bedrock_session(profile: str | None) -> object:
    import boto3  # noqa: PLC0415

    return boto3.Session(profile_name=profile)


async def _run_process(command: Sequence[str]) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_PROCESS_TIMEOUT_SECONDS,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return ProcessResult(
            returncode=124,
            stdout="",
            stderr=f"command timed out after {_PROCESS_TIMEOUT_SECONDS:g} seconds",
        )
    return ProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


__all__ = [
    "ConnectionResult",
    "ProcessResult",
    "ProviderChecker",
    "ProviderModel",
]
