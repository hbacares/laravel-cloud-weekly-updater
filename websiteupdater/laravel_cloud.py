"""Laravel Cloud REST API client.

This module implements the public Laravel Cloud API described at
https://cloud.laravel.com/docs/api/introduction.

Endpoint paths and request/response structures have been confirmed against the
official documentation and OpenAPI spec at https://cloud.laravel.com/api-docs/api.json.

Responsibilities
----------------
1. Create an ephemeral environment for the `autoupdate` branch.
2. Poll deployments for that env until success, failure, or timeout.
3. Return the env's public URL so the visual-diff step can screenshot it.
4. Tear the env down after the run.

The client is intentionally small. Every `**` comment marks a spot where a
doc-confirmed path or field name should be dropped in.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from websiteupdater.config import Settings
from websiteupdater.retry import retry_on_exception

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Endpoint paths — confirmed against https://cloud.laravel.com/docs/api
# ------------------------------------------------------------------
EP_LIST_APPS = "/applications"                                       # GET
EP_CREATE_ENV = "/environments"                                      # POST
EP_LIST_ENVS = "/applications/{project_id}/environments"             # GET
EP_DELETE_ENV = "/environments/{env_id}"                             # DELETE
EP_GET_ENV = "/environments/{env_id}"                                # GET
EP_LIST_DEPLOYMENTS = "/environments/{env_id}/deployments"           # GET
EP_GET_DEPLOYMENT = "/deployments/{deployment_id}"                   # GET

# Deployment state strings from the API.
# States: pending, build.pending, build.created, build.queued, build.running,
# build.succeeded, build.failed, deployment.pending, deployment.created,
# deployment.queued, deployment.running, deployment.succeeded, deployment.failed,
# cancelled, failed
TERMINAL_SUCCESS_STATES = {
    "succeeded", "success", "deployed", "done", "finished",
    "build.succeeded", "deployment.succeeded"
}
TERMINAL_FAILURE_STATES = {
    "failed", "error", "errored", "cancelled", "canceled",
    "build.failed", "deployment.failed"
}


class LaravelCloudError(RuntimeError):
    pass


@dataclass
class Application:
    id: str
    name: str
    slug: str
    raw: dict


@dataclass
class Deployment:
    id: str
    state: str
    url: Optional[str]
    raw: dict


@dataclass
class Environment:
    id: str
    name: str
    branch: str
    url: Optional[str]
    raw: dict


class LaravelCloudClient:
    def __init__(self, settings: Settings, *, timeout: float = 30.0):
        if not settings.laravel_cloud_token:
            raise LaravelCloudError(
                "LARAVEL_CLOUD_API_TOKEN is empty — set it in .env"
            )
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.laravel_cloud_base,
            headers={
                "Authorization": f"Bearer {settings.laravel_cloud_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    # ------------- low-level -------------

    @retry_on_exception(
        exceptions=(httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError),
        max_attempts=3,
        delay=2.0,
        backoff=2.0,
    )
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            # Retry on 5xx server errors, fail immediately on 4xx client errors
            if resp.status_code >= 500:
                logger.warning(f"{method} {path} → {resp.status_code}, will retry if attempts remain")
                raise httpx.HTTPStatusError(
                    f"{method} {path} → {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            raise LaravelCloudError(
                f"{method} {path} → {resp.status_code}: {resp.text[:500]}"
            )
        return resp

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LaravelCloudClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------- applications -------------

    def list_applications(self) -> list[Application]:
        """List all applications."""
        resp = self._request("GET", EP_LIST_APPS)
        data = resp.json()
        # Response is JSON:API format with data array
        if isinstance(data, dict) and "data" in data:
            apps_data = data["data"]
        else:
            apps_data = data if isinstance(data, list) else [data]
        return [self._parse_application(app) for app in apps_data]

    def find_application_by_slug(self, slug: str) -> Optional[Application]:
        """Find application by slug. Returns None if not found."""
        apps = self.list_applications()
        for app in apps:
            if app.slug == slug:
                return app
        return None

    def resolve_application_id(self, slug_or_id: str) -> str:
        """Resolve application slug or ID to ID.

        If the input looks like a UUID (starts with 'app-'), return it as-is.
        Otherwise, treat it as a slug and look it up.
        """
        if slug_or_id.startswith("app-"):
            return slug_or_id

        app = self.find_application_by_slug(slug_or_id)
        if not app:
            raise LaravelCloudError(
                f"Application not found with slug '{slug_or_id}'"
            )
        return app.id

    # ------------- environments -------------

    def create_ephemeral_environment(
        self, *, project_id: str, branch: str, env_name: Optional[str] = None
    ) -> Environment:
        """Create a new environment tracking `branch`.

        Args:
            project_id: The application ID (used as application_id in request)
            branch: The git branch name
            env_name: Optional custom environment name

        Returns the created Environment. The `url` field may be None until
        the first deployment finishes — use `wait_for_deployment` to resolve it.
        """
        name = env_name or f"autoupdate-{branch}".replace("/", "-")[:60]
        payload: dict[str, Any] = {
            "application_id": project_id,
            "name": name,
            "branch": branch,
        }
        resp = self._request("POST", EP_CREATE_ENV, json=payload)
        logger.debug(f"create_ephemeral_environment response: status={resp.status_code}, content={resp.text[:500]}")
        if not resp.text:
            raise LaravelCloudError(
                f"POST {EP_CREATE_ENV} returned empty response (status={resp.status_code})"
            )
        data = resp.json()
        # Response is JSON:API format with data.data or just data
        if isinstance(data, dict) and "data" in data:
            env = self._parse_env(data["data"])
        else:
            env = self._parse_env(data)
        return env

    def get_environment(self, *, project_id: str, env_id: str) -> Environment:
        """Get environment details. project_id is kept for API compatibility but unused."""
        resp = self._request("GET", EP_GET_ENV.format(env_id=env_id))
        data = resp.json()
        # Response is JSON:API format with data.data or just data
        if isinstance(data, dict) and "data" in data:
            return self._parse_env(data["data"])
        return self._parse_env(data)

    def delete_environment(self, *, project_id: str, env_id: str) -> None:
        """Delete environment. project_id is kept for API compatibility but unused."""
        self._request("DELETE", EP_DELETE_ENV.format(env_id=env_id))

    def list_environments(self, *, project_id: str) -> list[Environment]:
        """List all environments for a project."""
        resp = self._request("GET", EP_LIST_ENVS.format(project_id=project_id))
        data = resp.json()
        # Response is JSON:API format with data array
        if isinstance(data, dict) and "data" in data:
            envs_data = data["data"]
        else:
            envs_data = data if isinstance(data, list) else [data]
        return [self._parse_env(env) for env in envs_data]

    def find_environment_by_branch(self, *, project_id: str, branch: str) -> Optional[Environment]:
        """Find environment by branch name. Returns None if not found."""
        envs = self.list_environments(project_id=project_id)
        for env in envs:
            if env.branch == branch:
                return env
        return None

    def find_environment_by_name(self, *, project_id: str, name: str) -> Optional[Environment]:
        """Find environment by name. Returns None if not found."""
        envs = self.list_environments(project_id=project_id)
        for env in envs:
            if env.name == name:
                return env
        return None

    def resolve_environment_id(self, *, project_id: str, name_or_id: str) -> str:
        """Resolve environment name or ID to ID.

        If the input looks like a UUID (starts with 'env-'), return it as-is.
        Otherwise, treat it as a name and look it up.
        """
        if name_or_id.startswith("env-"):
            return name_or_id

        env = self.find_environment_by_name(project_id=project_id, name=name_or_id)
        if not env:
            raise LaravelCloudError(
                f"Environment not found with name '{name_or_id}' in project {project_id}"
            )
        return env.id

    def wait_for_environment_by_branch(
        self, *, project_id: str, branch: str, timeout: float = 300.0, poll_interval: float = 5.0
    ) -> Environment:
        """Poll until an environment exists for the given branch.

        Laravel Cloud auto-creates environments with names matching the branch name,
        but doesn't expose branch info in the API. Search by environment name instead.

        Raises LaravelCloudError on timeout or if environment never appears.
        """
        import time
        deadline = time.time() + timeout
        logger.info(f"Waiting for environment on branch {branch} (timeout={timeout}s)")

        while time.time() < deadline:
            # Laravel Cloud creates environments with name = branch name
            env = self.find_environment_by_name(project_id=project_id, name=branch)
            if env:
                logger.info(f"Found environment {env.id} (name={env.name}) for branch {branch}")
                return env
            time.sleep(poll_interval)

        raise LaravelCloudError(
            f"Timed out waiting {timeout}s for environment on branch {branch}"
        )

    # ------------- deployments -------------

    def latest_deployment(self, *, project_id: str, env_id: str) -> Deployment:
        """Get the most recent deployment for an environment.

        project_id is kept for API compatibility but unused.
        Lists deployments and returns the first (most recent).
        """
        resp = self._request(
            "GET",
            EP_LIST_DEPLOYMENTS.format(env_id=env_id),
            params={"per_page": 1},  # Only need the latest
        )
        data = resp.json()
        # Response is JSON:API paginated format with data array
        if isinstance(data, dict) and "data" in data:
            items = data["data"]
            if not items:
                raise LaravelCloudError(f"No deployments found for environment {env_id}")
            return self._parse_deployment(items[0])
        # Fallback if response is not in expected format
        raise LaravelCloudError(f"Unexpected response format: {data}")

    def wait_for_deployment(
        self,
        *,
        project_id: str,
        env_id: str,
        poll_interval: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Deployment:
        """Block until the env's latest deployment reaches a terminal state."""
        poll = poll_interval or self.settings.lc_poll_interval
        total = timeout or self.settings.lc_deploy_timeout
        deadline = time.monotonic() + total

        last: Optional[Deployment] = None
        while time.monotonic() < deadline:
            try:
                last = self.latest_deployment(project_id=project_id, env_id=env_id)
            except LaravelCloudError:
                # Deployment may not exist immediately after env creation.
                time.sleep(poll)
                continue

            state = (last.state or "").lower()
            if state in TERMINAL_SUCCESS_STATES:
                return last
            if state in TERMINAL_FAILURE_STATES:
                raise LaravelCloudError(
                    f"Deployment {last.id} reached terminal failure state: {last.state}"
                )
            time.sleep(poll)

        raise LaravelCloudError(
            f"Timed out after {total}s waiting for deployment on env {env_id}"
            + (f" (last state: {last.state})" if last else "")
        )

    def resolve_environment_url(
        self, *, project_id: str, env_id: str
    ) -> str:
        """Returns the public URL of the env, preferring the env object then the deployment."""
        env = self.get_environment(project_id=project_id, env_id=env_id)
        if env.url:
            return env.url
        dep = self.latest_deployment(project_id=project_id, env_id=env_id)
        if dep.url:
            return dep.url
        raise LaravelCloudError(
            f"No URL found for env {env_id} (env.raw={env.raw!r}, deployment.raw={dep.raw!r})"
        )

    # ------------- parsing -------------

    @staticmethod
    def _parse_application(data: dict) -> Application:
        """Parse application from JSON:API response."""
        attrs = data.get("attributes", {})
        source = attrs if attrs else data

        return Application(
            id=str(data.get("id") or ""),
            name=str(source.get("name", "")),
            slug=str(source.get("slug", "")),
            raw=data,
        )

    @staticmethod
    def _parse_env(data: dict) -> Environment:
        """Parse environment from JSON:API response.

        Response may have attributes nested in data.attributes for JSON:API format,
        or flat structure depending on endpoint.
        """
        # Handle JSON:API format with attributes
        attrs = data.get("attributes", {})
        # Prefer attributes, fall back to root level
        source = attrs if attrs else data

        return Environment(
            id=str(data.get("id") or data.get("uuid") or ""),
            name=str(source.get("name", "")),
            branch=str(source.get("branch", "")),
            url=_pick_url(source),
            raw=data,
        )

    @staticmethod
    def _parse_deployment(data: dict) -> Deployment:
        """Parse deployment from JSON:API response.

        The API uses 'status' field for deployment state, with values like:
        deployment.succeeded, deployment.failed, build.succeeded, etc.
        """
        # Handle JSON:API format with attributes
        attrs = data.get("attributes", {})
        # Prefer attributes, fall back to root level
        source = attrs if attrs else data

        return Deployment(
            id=str(data.get("id") or data.get("uuid") or ""),
            state=str(source.get("status") or source.get("state") or ""),
            url=_pick_url(source),
            raw=data,
        )


def _pick_url(data: dict, _depth: int = 0) -> Optional[str]:
    """Extract URL from Laravel Cloud API response.

    Laravel Cloud environments have vanity_domain field.
    May also have url, public_url, or nested in relationships.
    """
    # Prevent infinite recursion
    if _depth > 3:
        return None

    # Check direct URL fields, prioritizing vanity_domain for Laravel Cloud
    for key in ("vanity_domain", "url", "public_url", "preview_url", "hostname", "domain"):
        v = data.get(key)
        if v:
            s = str(v)
            if not s.startswith(("http://", "https://")):
                s = "https://" + s
            return s

    # Check nested in attributes (JSON:API format)
    attrs = data.get("attributes", {})
    if isinstance(attrs, dict) and attrs is not data:  # Avoid self-reference
        url = _pick_url(attrs, _depth + 1)
        if url:
            return url

    # Check nested in environment
    nested = data.get("environment")
    if isinstance(nested, dict) and nested is not data:  # Avoid self-reference
        return _pick_url(nested, _depth + 1)

    # Check domains array
    domains = data.get("domains")
    if isinstance(domains, list) and domains:
        first = domains[0]
        if isinstance(first, dict):
            return _pick_url(first, _depth + 1)
        if isinstance(first, str):
            return first if first.startswith("http") else f"https://{first}"

    return None
