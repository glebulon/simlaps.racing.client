"""Additional coverage tests for src.core.pb_cache."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.pb_cache import PBCache, get_pb_cache


@pytest.mark.asyncio
async def test_preload_from_api_handles_non_200_response() -> None:
    cache = PBCache("https://simlaps.racing")

    with patch("httpx.AsyncClient") as mock_client:
        response = MagicMock()
        response.status_code = 503
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)

        ok = await cache.preload_from_api("76561198000000000")

    assert ok is False
    assert cache.is_loaded() is False


@pytest.mark.asyncio
async def test_preload_from_api_handles_timeout_and_request_error() -> None:
    cache = PBCache("https://simlaps.racing")

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        assert await cache.preload_from_api("steam") is False

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.RequestError("request")
        )
        assert await cache.preload_from_api("steam") is False


@pytest.mark.asyncio
async def test_preload_from_api_skips_invalid_rows_and_bad_timestamps() -> None:
    cache = PBCache("https://simlaps.racing")

    payload = {
        "personalBests": [
            {"trackId": "spa", "carId": "car", "bestTime": 120000, "setAt": "not-a-date"},
            {"trackId": "", "carId": "car", "bestTime": 120000},
            {"trackId": "spa", "carId": "", "bestTime": 120000},
            {"trackId": "spa", "carId": "car2", "bestTime": 0},
        ]
    }

    with patch("httpx.AsyncClient") as mock_client:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)

        ok = await cache.preload_from_api("steam")

    assert ok is True
    assert cache.is_loaded() is True
    all_pbs = cache.get_all_pbs()
    assert len(all_pbs) == 1
    assert ("spa", "car") in all_pbs


def test_get_pb_cache_reuses_and_replaces_singleton_by_server_url() -> None:
    cache1 = get_pb_cache("https://a.example")
    cache2 = get_pb_cache("https://a.example")
    cache3 = get_pb_cache("https://b.example")

    assert cache1 is cache2
    assert cache3 is not cache2
