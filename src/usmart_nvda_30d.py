#!/usr/bin/env python3
"""Read-only uSMART SG NVDA daily K-lines for the previous 30 calendar days.

Reuses the previously verified V8 login implementation from the same directory.
No account asset changes, trading, trade-login, or order endpoints are called.
"""
import argparse
import base64
import csv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import getpass
import json
import re
import secrets
import sys
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

try:
    from usmart_v8_asset_diagnostic import load_key, signed_post, HOST, LOGIN
except ImportError:
    raise SystemExit("Missing usmart_v8_asset_diagnostic.py in the same directory. Copy this file next to the working V8 script.")

DEFAULT_DIR = Path.home() / "Documents" / "usmart"
QUOTE_HOST = "https://open-hz.usmartsg.com:8443"
KLINE_PATH = "/quotes-openservice/api/v1/kline"


def quote_post(session, payload, channel, signing_key, token):
    # Same algorithm as official demo's RequestUtil.post_with_sign_by_quote.
    body = json.dumps(payload, ensure_ascii=False)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": token,
        "X-Channel": channel,
        "X-Lang": "3",
        "X-Request-Id": str(time.time_ns())[:16] + f"{secrets.randbelow(1000):03d}",
        "X-Time": str(int(time.time())),
    }
    to_sign = "".join(headers[k] for k in ("Authorization", "X-Channel", "X-Lang", "X-Request-Id", "X-Time")) + body
    headers["X-Sign"] = base64.urlsafe_b64encode(
        signing_key.sign(to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.MD5())
    ).decode("ascii")
    res = session.post(QUOTE_HOST + KLINE_PATH, data=body.encode("utf-8"), headers=headers, timeout=(8, 30))
    if res.status_code != 200:
        raise RuntimeError(f"Quote API returned HTTP {res.status_code} (response body not printed)")
    try:
        result = res.json()
    except ValueError:
        raise RuntimeError("Quote API returned non-JSON response (body not printed)")
    if not isinstance(result, dict):
        raise RuntimeError("Quote API returned unexpected JSON shape")
    return result


def as_date(ts):
    # uSMART documented latestTime example: 20191224093000000.
    s = str(ts or "")
    if re.fullmatch(r"\d{8,17}", s) and s[:4].isdigit() and 1990 <= int(s[:4]) <= 2100:
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except ValueError:
            pass
    if s.isdigit() and len(s) in (10, 13):
        try:
            return datetime.fromtimestamp(int(s) / (1000 if len(s)==13 else 1), timezone.utc).date()
        except (ValueError, OverflowError):
            pass
    return None


def main():
    p = argparse.ArgumentParser(description="Login via working uSMART V8 and download last 30 calendar days of US NVDA daily OHLCV")
    p.add_argument("--folder", type=Path, default=DEFAULT_DIR, help="Parent folder with public_key/private_key/api_code")
    p.add_argument("--secu-id", default="usNVDA", help="uSMART market-prefixed ticker identifier (default: usNVDA; confirm if API rejects)")
    p.add_argument("--days", type=int, default=30, help="Calendar days including today (default: 30)")
    p.add_argument("--count", type=int, default=65, help="Daily candles to request, to cover days off (default: 65)")
    p.add_argument("--output", type=Path, default=Path.home()/"Documents"/"usmart"/"nvda_daily_30d.csv")
    a = p.parse_args()
    if not re.fullmatch(r"us[A-Za-z0-9._-]{1,24}", a.secu_id) or not 1 <= a.days <= 365 or not 1 <= a.count <= 200:
        p.error("Invalid --secu-id, --days or --count")
    folder = a.folder.expanduser()
    channel = (folder / "api_code").read_text().strip()
    if not re.fullmatch(r"[0-9A-Za-z_-]{3,32}", channel):
        raise RuntimeError("Invalid API Code format")
    pub = load_key(folder / "public_key", private=False)
    priv = load_key(folder / "private_key", private=True)
    area = input("Country calling code (e.g. 66): ").strip().lstrip("+")
    phone = input("Registered uSMART login phone (without country calling code): ").strip().replace(" ", "")
    if not re.fullmatch(r"\d{1,4}", area) or not re.fullmatch(r"\d{6,16}", phone):
        raise RuntimeError("Invalid phone number format")
    password = getpass.getpass("uSMART LOGIN password (hidden; NOT trade PIN): ")
    if not password:
        raise RuntimeError("Empty password")
    encrypt = lambda v: base64.urlsafe_b64encode(pub.encrypt(v.encode(), padding.PKCS1v15())).decode()
    login_payload = {"phoneNumber": encrypt(phone), "password": encrypt(password), "areaCode": area}
    del password
    with requests.Session() as sess:
        print("1/2 Signing in using verified V8 login flow...")
        login = signed_post(sess, LOGIN, login_payload, channel, priv, x_type="12")
        if login.get("code") != 0:
            print(f"Login rejected: API code {login.get('code')} (message intentionally hidden)")
            return 1
        token = (login.get("data") or {}).get("token")
        if not token:
            print("Login succeeded but token was not returned; stopping")
            return 1
        print("PASS: Login succeeded. Token not displayed or saved.")
        print(f"2/2 Requesting daily K-lines for {a.secu_id} (type=7, right=0, start=0)...")
        out = quote_post(sess, {"secuId":a.secu_id,"type":7,"start":0,"right":0,"count":a.count},channel,priv,token)
    if out.get("code") != 0:
        print("Quote API rejected request. Code:",out.get("code"))
        msg = str(out.get("msg") or "")
        # Limit to non-sensitive error diagnostics; never expose token.
        msg = re.sub(r"\b\d{5,}\b", "[REDACTED]", msg)
        msg = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", msg)
        print("Broker message:", msg[:160] or "(empty)")
        print("Check quote entitlements / IP whitelist / ticker identifier if necessary.")
        return 2
    data = out.get("data") or {}
    records = data.get("list", []) if isinstance(data,dict) else []
    if not isinstance(records,list) or not records:
        print("API code=0 but returned no daily candles. Available data keys:",list(data.keys()) if isinstance(data,dict) else [])
        return 3
    end = datetime.now(ZoneInfo("America/New_York")).date()
    begin = end - timedelta(days=a.days-1)
    byday={}
    for r in records:
        if not isinstance(r,dict):
            continue
        dt=as_date(r.get("latestTime"))
        if dt is None or not begin<=dt<=end:
            continue
        byday[dt]={"date":dt.isoformat(),"ticker":"NVDA","secuId":a.secu_id,"open":r.get("open"),"high":r.get("high"),"low":r.get("low"),"close":r.get("close"),"volume":r.get("volume"),"amount":r.get("amount"),"preClose":r.get("preClose"),"latestTime":r.get("latestTime")}
    rows=[byday[k] for k in sorted(byday)]
    if not rows:
        print(f"Received {len(records)} candles, but none within {begin} to {end}. LatestTime examples:", [str(x.get("latestTime")) for x in records[:3] if isinstance(x,dict)])
        return 4
    target=a.output.expanduser()
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
        writer.writeheader(); writer.writerows(rows)
    print(f"PASS: Saved {len(rows)} trading-day candles within {a.days} calendar days ({begin} to {end})")
    print("CSV:",target)
    print("First date:",rows[0]["date"],"Last date:",rows[-1]["date"])
    print("Latest close:",rows[-1]["close"],"(currency/adjustment: verify against broker; right=0 means unadjusted)")
    print("No orders, trade login or account modifications performed.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, requests.RequestException) as ex:
        print("STOPPED SAFELY:",str(ex).splitlines()[0][:200],file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled; no orders sent.",file=sys.stderr)
        sys.exit(130)