import sys
import webbrowser
import requests
from urllib.parse import urlencode, parse_qs, urlparse

_BASE = "https://api.upstox.com/v2"
_REDIRECT_URI = "https://127.0.0.1"


def get_auth_url(api_key: str) -> str:
    params = {"response_type": "code", "client_id": api_key, "redirect_uri": _REDIRECT_URI}
    return f"{_BASE}/login/authorization/dialog?{urlencode(params)}"


def exchange_code(api_key: str, api_secret: str, code: str) -> str:
    resp = requests.post(
        f"{_BASE}/login/authorization/token",
        data={
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": _REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def validate_token(token: str) -> bool:
    resp = requests.get(
        f"{_BASE}/user/profile",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10,
    )
    return resp.status_code == 200


def save_token_to_env(token: str, env_path: str = ".env"):
    with open(env_path, "r") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith("UPSTOX_ACCESS_TOKEN="):
            lines[i] = f"UPSTOX_ACCESS_TOKEN={token}\n"
            break
    else:
        lines.append(f"\nUPSTOX_ACCESS_TOKEN={token}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


if __name__ == "__main__":
    import config
    url = get_auth_url(config.UPSTOX_API_KEY)
    print(f"Opening browser for Upstox login...")
    print(f"If browser doesn't open: {url}")
    webbrowser.open(url)
    redirect = input("Paste the full redirect URL after login: ").strip()
    code = parse_qs(urlparse(redirect).query).get("code", [None])[0]
    if not code:
        print("No code found in URL.")
        sys.exit(1)
    token = exchange_code(config.UPSTOX_API_KEY, config.UPSTOX_API_SECRET, code)
    save_token_to_env(token)
    print(f"Token saved. Starts with: {token[:20]}...")
