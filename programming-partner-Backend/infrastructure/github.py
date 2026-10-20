from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from domain.errors import ApiError


class GitHubClient:
    """Adaptador de lectura de GitHub; las operaciones de escritura siguen apagadas."""

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("GITHUB_TOKEN"))

    def repositories(self) -> list[dict]:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ApiError(503, "github_not_configured", "Configura GITHUB_TOKEN para consultar GitHub.")
        request = Request(
            "https://api.github.com/user/repos?sort=updated&per_page=30",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "programming-partner-local",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            return [{
                "name": repo["full_name"], "private": repo["private"],
                "url": repo["html_url"], "default_branch": repo["default_branch"],
            } for repo in payload]
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ApiError(502, "github_auth_failed", "GitHub rechazó el token o sus permisos.")
            raise ApiError(502, "github_error", f"GitHub respondió con HTTP {exc.code}.")
        except (URLError, TimeoutError):
            raise ApiError(502, "github_unavailable", "No se pudo conectar con GitHub.")
