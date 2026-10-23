"""Read official plan usage without exporting credentials or changing Claude.

macOS only. The Desktop cookie database is opened read-only; its session cookie
is decrypted in memory with the existing Keychain key. Requests are restricted
to one fixed HTTPS origin. No redirects, login refresh, model calls or writes.
Only normalized quota numbers, reset times and safe error codes leave here.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


class UsageError(Exception):
    pass


def keychain_password():
    for account in ("Claude", "Claude Key"):
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", account,
             "-s", "Claude Safe Storage", "-w"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=8)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.rstrip(b"\r\n")
    raise UsageError("KEYCHAIN_UNAVAILABLE")


def decrypt_cookie(host, encrypted, password, schema):
    if not encrypted.startswith(b"v10"):
        raise UsageError("COOKIE_FORMAT_UNSUPPORTED")
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
    crypto = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    crypt = crypto.CCCrypt
    crypt.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    crypt.restype = ctypes.c_int32
    payload = encrypted[3:]
    output = ctypes.create_string_buffer(len(payload) + 16)
    written = ctypes.c_size_t()
    # kCCDecrypt=1, kCCAlgorithmAES=0, kCCOptionPKCS7Padding=1.
    status = crypt(1, 0, 1, key, len(key), b" " * 16, payload, len(payload),
                   output, len(output), ctypes.byref(written))
    if status:
        raise UsageError("COOKIE_DECRYPT_FAILED")
    plain = output.raw[:written.value]
    if schema >= 24:
        digest = hashlib.sha256(host.encode()).digest()
        if not plain.startswith(digest):
            raise UsageError("COOKIE_HOST_MISMATCH")
        plain = plain[32:]
    if not plain or any(byte < 0x21 or byte > 0x7e or byte in (0x3b, 0x0a, 0x0d)
                        for byte in plain):
        raise UsageError("COOKIE_INVALID")
    return plain.decode("ascii")


def credentials(profile):
    # Never follow a profile symlink into an unrelated login directory.
    if not profile.is_absolute() or any(p.is_symlink() for p in [profile, *profile.parents]):
        raise UsageError("PROFILE_INVALID")
    database = profile / "Cookies"
    if database.is_symlink() or not database.is_file():
        raise UsageError("NO_SESSION")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        schema = int(row[0]) if row else 0
        rows = db.execute(
            "SELECT host_key,name,value,encrypted_value,expires_utc FROM cookies "
            "WHERE name IN ('sessionKey','lastActiveOrg') "
            "AND host_key IN ('claude.ai','.claude.ai') AND path='/' "
            "ORDER BY last_access_utc DESC").fetchall()
    values = {}
    password = None
    for host, name, plain, encrypted, expires in rows:
        if name in values:
            continue
        if expires and expires / 1000000 - 11644473600 < time.time():
            continue
        if not plain and encrypted:
            if password is None:
                password = keychain_password()
            plain = decrypt_cookie(host, bytes(encrypted), password, schema)
        if plain:
            values[name] = plain
    session = values.get("sessionKey", "")
    org = values.get("lastActiveOrg", "")
    if not session:
        raise UsageError("NO_SESSION")
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", org):
        raise UsageError("NO_ORGANIZATION")
    if any(c in session for c in "\r\n;"):
        raise UsageError("COOKIE_INVALID")
    return session, org


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise UsageError("REDIRECT_REFUSED")


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return max(0, min(100, round(value)))


def reset_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


def normalize(body):
    if not isinstance(body, dict):
        raise UsageError("INVALID_RESPONSE")
    five = body.get("five_hour") or {}
    week = body.get("seven_day") or {}
    if not isinstance(five, dict) or not isinstance(week, dict):
        raise UsageError("INVALID_RESPONSE")
    fh, sd = percent(five.get("utilization")), percent(week.get("utilization"))
    if fh is None and sd is None:
        raise UsageError("NO_QUOTA_DATA")
    return {"at": int(time.time() * 1000), "fh": fh, "sd": sd,
            "fhResetAt": reset_time(five.get("resets_at")),
            "sdResetAt": reset_time(week.get("resets_at")), "source": "official"}


def refresh(profile, opener=None):
    if sys.platform != "darwin":
        raise UsageError("PLATFORM_UNSUPPORTED")
    session, org = credentials(profile)
    request = urllib.request.Request(
        "https://claude.ai/api/organizations/" + org + "/usage?skip_spend=1",
        headers={"Accept": "application/json", "User-Agent": "CASE/4.2.0",
                 "Cookie": "sessionKey=" + session + "; lastActiveOrg=" + org,
                 "Origin": "https://claude.ai", "X-Organization-Uuid": org,
                 "Referer": "https://claude.ai/settings/usage"})
    opener = opener or urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=12) as response:
            if response.geturl() != request.full_url:
                raise UsageError("REDIRECT_REFUSED")
            payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise UsageError("INVALID_RESPONSE")
        return normalize(json.loads(payload))
    except urllib.error.HTTPError as error:
        # Never log the response body, cookie, URL or account identifiers.
        if error.headers.get("cf-mitigated", "").lower() == "challenge":
            raise UsageError("BROWSER_VERIFICATION_REQUIRED") from None
        raise UsageError("HTTP_" + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UsageError("NETWORK_ERROR") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise UsageError("INVALID_RESPONSE") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = {"ok": True, "usage": refresh(args.profile)}
    except UsageError as error:
        result = {"ok": False, "code": str(error)}
    except subprocess.TimeoutExpired:
        result = {"ok": False, "code": "KEYCHAIN_TIMEOUT"}
    except Exception:
        result = {"ok": False, "code": "LOCAL_READ_FAILED"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
