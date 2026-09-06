"""Hash and optionally submit an ACE Career Mode payload.

The payload hash is kept compatible with the ACE web client's object-hash
representation. Submission is an opt-in operation: callers must provide a
token explicitly, and the command-line entry point reads it from the
environment rather than storing credentials in source control.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

import httpx

# Fixed prefix that object-hash injects for a plain object with respectType=true
_META = (
    "string:9:prototype:Undefined,"
    "string:9:__proto__:object:3:"
    "string:9:prototype:Undefined,"
    "string:9:__proto__:Null,"
    "string:11:constructor:fn:"
    "string:8:[native]"
    "string:20:function-name:Object"
    "object:0:,,"
    "string:11:constructor:fn:"
    "string:8:[native]"
    "string:20:function-name:Object"
    "string:12:[CIRCULAR:2],"
)

SUBMIT_URL = "https://app.acecareermode.com/functions/licenses/submitLicenseResult"
REQUEST_TIMEOUT_SECONDS = 15.0
TOKEN_ENVIRONMENT_VARIABLE = "ACE_CAREER_MODE_TOKEN"  # noqa: S105


def _serialize_value(v: Any) -> str:
    if isinstance(v, str):
        return f"string:{len(v)}:{v}"
    if isinstance(v, int):
        return f"number:{v}"
    raise TypeError(f"Unsupported type: {type(v)}")


def compute_payload_hash(payload: dict) -> str:
    """Compute the ACE Career Mode payload hash."""
    core_fields = {
        "replayDate": payload["replayDate"],
        "replayTime": payload["replayTime"],
        "carID": payload["carID"],
        "carPreset": payload["carPreset"],
        "trackID": payload["trackID"],
        "layout": payload["layout"],
        "bestTimeMs": payload["bestTimeMs"],
        "steamId": payload["steamId"],
    }
    keys = sorted(core_fields.keys())
    parts = [f"object:{len(keys) + 3}:"]
    parts.append(_META)
    for k in keys:
        parts.append(f"string:{len(k)}:{k}:")
        parts.append(_serialize_value(core_fields[k]))
        parts.append(",")
    serialized = "".join(parts)
    # SHA-1 is part of ACE's existing payload-hash protocol.
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()  # noqa: S324


def _response_result(response: httpx.Response) -> dict:
    """Return a bounded, dictionary-shaped result for any HTTP response."""
    if not 200 <= response.status_code < 300:
        try:
            body = response.json()
        except (ValueError, TypeError):
            return {
                "error": "http_error",
                "status_code": response.status_code,
                "text": response.text[:500],
            }
        if isinstance(body, dict):
            return {
                "error": "http_error",
                "status_code": response.status_code,
                "response": body,
            }
        return {
            "error": "http_error",
            "status_code": response.status_code,
            "response": body,
        }
    try:
        result = response.json()
    except (ValueError, TypeError):
        return {
            "status_code": response.status_code,
            "text": response.text[:500],
        }
    if isinstance(result, dict):
        return result
    return {"status_code": response.status_code, "error": "invalid_response"}


def submit_payload(payload: dict, token: str | None) -> dict:
    """Submit a payload using a caller-provided bearer token.

    Missing credentials are rejected before hashing or network access. The
    return value is always a dictionary with stable local error codes for
    transport failures.
    """
    if not isinstance(token, str) or not token.strip():
        return {"error": "missing_token"}

    full = {**payload, "payloadHash": compute_payload_hash(payload)}
    test_id = payload["testID"]

    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "origin": "https://app.acecareermode.com",
        "referer": f"https://app.acecareermode.com/licenses/{test_id.split('-')[0]}/{test_id}",
    }

    try:
        response = httpx.post(
            SUBMIT_URL,
            headers=headers,
            json=full,
            timeout=REQUEST_TIMEOUT_SECONDS,
            verify=True,
        )
    except httpx.TimeoutException:
        return {"error": "request_timeout"}
    except httpx.RequestError:
        return {"error": "network_error"}

    return _response_result(response)


def _load_payload(path: Path) -> dict:
    with path.open(encoding="utf-8") as payload_file:
        payload = json.load(payload_file)
    if not isinstance(payload, dict):
        raise ValueError("payload JSON must contain an object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Submit a payload JSON file using a token from an environment variable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "payload", type=Path, help="JSON file containing the ACE payload"
    )
    parser.add_argument(
        "--token-env",
        default=TOKEN_ENVIRONMENT_VARIABLE,
        help=(
            "environment variable containing the bearer token "
            f"(default: {TOKEN_ENVIRONMENT_VARIABLE})"
        ),
    )
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env)
    if not token:
        parser.error(f"environment variable {args.token_env} is not set")

    try:
        payload = _load_payload(args.payload)
    except (OSError, ValueError) as exc:
        parser.error(f"unable to read payload JSON: {exc}")

    result = submit_payload(payload, token)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if "error" not in result else 1

if __name__ == "__main__":
    raise SystemExit(main())
