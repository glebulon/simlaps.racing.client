import hashlib
import json

import requests

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


def _serialize_value(v):
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
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def submit_payload(payload: dict, token: str) -> dict:
    """Submit the license result to the ACE Career Mode server."""
    full = {**payload, "payloadHash": compute_payload_hash(payload)}

    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "origin": "https://app.acecareermode.com",
        "priority": "u=1, i",
        "referer": f"https://app.acecareermode.com/licenses/{payload['testID'].split('-')[0]}/{payload['testID']}",
        "sec-ch-ua": '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
    }

    resp = requests.post(
        "https://app.acecareermode.com/functions/licenses/submitLicenseResult",
        headers=headers,
        json=full,
        verify=False,
    )
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


if __name__ == "__main__":
    raise SystemExit(
        "Import this helper and call compute_payload_hash(payload) or "
        "submit_payload(payload, token) with your own payload and token."
    )
