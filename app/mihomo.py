import os
from pathlib import Path

import httpx

from .db import get_setting, mihomo_home


def api_base():
    return os.environ.get("MIHOMO_API", "http://127.0.0.1:19090")


def headers():
    secret = get_setting("mihomo_secret", "")
    data = {"Accept": "application/json"}
    if secret:
        data["Authorization"] = f"Bearer {secret}"
    return data


def client():
    return httpx.Client(base_url=api_base(), headers=headers(), timeout=20)


async def async_client():
    return httpx.AsyncClient(base_url=api_base(), headers=headers(), timeout=30)


def binary_path() -> Path:
    return mihomo_home() / "mihomo"


def config_dir() -> Path:
    return mihomo_home() / "config"


def config_path() -> Path:
    return config_dir() / "config.yaml"
