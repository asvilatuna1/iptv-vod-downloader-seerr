"""Client helpers for interacting with Xtream Codes compatible IPTV APIs and Seerr."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.5993.70 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class APIError(RuntimeError):
    """Raised when the API returns an unexpected payload."""


def _normalise_base_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if url.endswith("/player_api.php"):
        url = url[: -len("/player_api.php")]
    return url.rstrip("/")

# NUEVA CLASE: Cliente de Jellyseerr
class SeerrClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({"X-Api-Key": self.api_key, "Accept": "application/json"})
        self.timeout = 5

    def check_availability(self, tmdb_id: int, media_type: str) -> bool:
        if not tmdb_id:
            return False
        try:
            url = f"{self.base_url}/api/v1/{media_type}/{tmdb_id}"
            resp = self._session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                media_info = data.get("mediaInfo", {})
                return media_info.get("status") in [4, 5]
            return False
        except Exception:
            return False


class IPTVClient:
    """Wraps common Xtream Codes VOD and series endpoints."""

    def __init__(self, base_url: str, username: str, password: str, seerr_url: Optional[str] = None, seerr_key: Optional[str] = None) -> None:
        self.base_url = _normalise_base_url(base_url)
        self.username = username
        self.password = password
        self.api_url = f"{self.base_url}/player_api.php"
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self.timeout = (5, 60)
        
        # Inicializar Seerr si se configuró
        self.seerr = None
        if seerr_url and seerr_key:
            self.seerr = SeerrClient(seerr_url, seerr_key)

    def _request(self, **params: Any) -> Any:
        payload = {
            "username": self.username,
            "password": self.password,
        }
        payload.update(params)

        logger.debug("Requesting %s with %s", self.api_url, payload)
        resp = self._session.get(self.api_url, params=payload, timeout=self.timeout)
        resp.raise_for_status()

        data: Any = resp.json()

        if isinstance(data, dict):
            user_info = data.get("user_info")
            if isinstance(user_info, dict) and int(user_info.get("auth", 0)) != 1:
                status = user_info.get("status", "unauthorised")
                raise APIError(f"Authentication failed: {status}")

        return data

    def check_connection(self) -> Dict[str, Any]:
        data = self._request()
        if not isinstance(data, dict):
            raise APIError("Unexpected response while checking connection.")
        return data

    def get_vod_categories(self) -> List[Dict[str, Any]]:
        data = self._request(action="get_vod_categories")
        if not isinstance(data, list):
            raise APIError("Unexpected payload for VOD categories.")
        return sorted(data, key=lambda item: item.get("category_name", "").lower())

    def get_vod_streams(self, category_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"action": "get_vod_streams"}
        if category_id and category_id != "0":
            params["category_id"] = category_id
        data = self._request(**params)
        if not isinstance(data, list):
            raise APIError("Unexpected payload for VOD streams.")
            
        # NUEVO: Comprobar en Seerr
        if self.seerr:
            for item in data:
                tmdb_id = item.get("tmdb_id")
                item["exists_in_seerr"] = self.seerr.check_availability(tmdb_id, "movie") if tmdb_id else False
                
        return data

    def get_vod_info(self, stream_id: str) -> Dict[str, Any]:
        data = self._request(action="get_vod_info", vod_id=stream_id)
        if not isinstance(data, dict):
            raise APIError("Unexpected payload for VOD info.")
        return data

    def get_series_categories(self) -> List[Dict[str, Any]]:
        data = self._request(action="get_series_categories")
        if not isinstance(data, list):
            raise APIError("Unexpected payload for series categories.")
        return sorted(data, key=lambda item: item.get("category_name", "").lower())

    def get_series(self, category_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"action": "get_series"}
        if category_id and category_id != "0":
            params["category_id"] = category_id
        data = self._request(**params)
        if not isinstance(data, list):
            raise APIError("Unexpected payload for series list.")
            
        # NUEVO: Comprobar en Seerr
        if self.seerr:
            for item in data:
                tmdb_id = item.get("tmdb_id")
                item["exists_in_seerr"] = self.seerr.check_availability(tmdb_id, "tv") if tmdb_id else False
                
        return data

    def get_series_info(self, series_id: str) -> Dict[str, Any]:
        data = self._request(action="get_series_info", series_id=series_id)
        if not isinstance(data, dict):
            raise APIError("Unexpected payload for series info.")
        return data

    def build_movie_stream_url(self, stream_id: str, extension: Optional[str]) -> str:
        ext = extension or "mp4"
        return f"{self.base_url}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def build_episode_stream_url(self, episode_id: str, extension: Optional[str]) -> str:
        ext = extension or "mp4"
        return f"{self.base_url}/series/{self.username}/{self.password}/{episode_id}.{ext}"

    def fetch_resource(self, url: str) -> bytes:
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content
