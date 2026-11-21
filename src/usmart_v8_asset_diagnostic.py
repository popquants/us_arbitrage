#!/usr/bin/env python3
"""uSMART SG: Login with official-demo X-Type -> US stock asset (read-only) V8. No order methods.

Based on the uploaded official Python demo's common/req_util.py, with standard
RSA DER/PEM key parsing (its common/utils.py reverses PEM headers).
"""
import argparse
import base64
import getpass
import json
import re
import sys
import time
import secrets
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HOST = 'https://open-jy.usmartsg.com'  # production HTTPS host in uploaded README
LOGIN = '/user-server-sg/open-api/login'
ASSETS = '/asset-center-sg/open-api/stock-asset'  # corrected spelling; official docs contain 'asstet' typo; not yet live-verified
DEFAULT_DIR = Path.home() / 'Documents' / 'usmart'


def load_key(path, private):
    raw = path.read_bytes().strip()
    if raw.startswith(b'-----BEGIN'):
        der_or_pem = raw
        fmt = 'pem'
    else:
        try:
            der_or_pem = base64.b64decode(re.sub(rb'\s+', b'', raw), validate=True)
        except Exception as e:
            raise ValueError(f'Invalid Base64 in {path.name}') from e
        fmt = 'der'
    loader = (serialization.load_pem_private_key if fmt == 'pem' else serialization.load_der_private_key) if private else (serialization.load_pem_public_key if fmt == 'pem' else serialization.load_der_public_key)
    try:
        key = loader(der_or_pem, password=None) if private else loader(der_or_pem)
    except Exception as e:
        raise ValueError(f'Cannot parse {path.name} as a {"private" if private else "public"} RSA key') from e
    if not isinstance(key, (rsa.RSAPrivateKey if private else rsa.RSAPublicKey)):
        raise ValueError(f'{path.name} is not an RSA key')
    return key


def signed_post(session, path, body, channel, signing_key, token=None, x_type="12"):
    # Match uploaded demo: sign exactly the serialized bytes sent to the server.
    payload = json.dumps(body, ensure_ascii=False)
    signature = base64.b64encode(signing_key.sign(payload.encode('utf-8'), padding.PKCS1v15(), hashes.MD5())).decode('ascii')
    headers = {'Content-Type': 'application/json; charset=utf-8',
               'X-Lang': '3', 'X-Channel': channel, 'X-Sign': signature,
               'X-Type': x_type, 'X-Dt': '1',
               'X-Request-Id': str(time.time_ns())[:19],
               'X-Time': str(int(time.time()))}
    if token:
        headers['Authorization'] = token
    response = session.post(HOST + path, data=payload.encode('utf-8'), headers=headers, timeout=(8, 20))
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f'Unexpected response: HTTP {response.status_code}, non-JSON (body intentionally hidden)')
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}; API code: {data.get("code", "unknown")} (body intentionally hidden)')
    return data


def main():
    p = argparse.ArgumentParser(description='uSMART SG login and read-only US stock balance')
    p.add_argument('--folder', type=Path, default=DEFAULT_DIR, help='directory containing public_key, private_key, api_code')
    p.add_argument('--check', action='store_true', help='validate key files without network calls')
    p.add_argument('--balance', action='store_true', help='login and query account asset, read-only')
    p.add_argument('--x-type', default='12', choices=['12','1','2'], help='uSMART client type (official SG demo config has 12; default: 12)')
    a = p.parse_args()
    if a.check == a.balance:
        p.error('Choose exactly one of --check or --balance')
    folder = a.folder.expanduser()
    channel = (folder / 'api_code').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[0-9A-Za-z_-]{3,32}', channel):
        raise ValueError('api_code has invalid format (not printing its contents)')
    public = load_key(folder / 'public_key', private=False)
    private = load_key(folder / 'private_key', private=True)
    # The uploaded official demo uses the supplied public key for encrypting login data
    # and supplied private key for signing. They need NOT be an RSA pair.
    probe = b'popquants-local-check'
    private.public_key().verify(private.sign(probe, padding.PKCS1v15(), hashes.MD5()), probe, padding.PKCS1v15(), hashes.MD5())
    print('PASS: keys parse, private-key signing works; API Code format valid')
    print('Info: public_key is used for login encryption; private_key is used for request signing.')
    if a.check:
        print('Offline check only; no network request made.')
        return
    print('Mode: LOGIN + READ US STOCK ASSETS ONLY. No trade password or order calls.')
    print('Login headers: X-Type=' + a.x_type + ', X-Dt=1, X-Request-Id, X-Time; X-Channel read from api_code (not displayed).')
    area = input('Country phone calling code (e.g. 66 for Thailand): ').strip().lstrip('+')
    phone = input('Registered uSMART login phone number (without area code): ').strip()
    if not re.fullmatch(r'\d{1,4}', area) or not re.fullmatch(r'\d{6,16}', phone):
        raise ValueError('Phone/area code format invalid')
    passwd = getpass.getpass('uSMART LOGIN password (hidden; not trade PIN): ')
    if not passwd:
        raise ValueError('Empty password')
    def enc(s):
        return base64.urlsafe_b64encode(public.encrypt(s.encode('utf-8'), padding.PKCS1v15())).decode('ascii')
    login_body = {'phoneNumber': enc(phone), 'password': enc(passwd), 'areaCode': area}
    del passwd
    with requests.Session() as session:
        print('1/2 Requesting login token...')
        login = signed_post(session, LOGIN, login_body, channel, private, x_type=a.x_type)
        if login.get('code') != 0:
            msg = str(login.get('msg') or login.get('message') or '')
            # Avoid printing phone numbers, credentials or tokens from broker messages.
            msg = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]+', r'\1[REDACTED]', msg)
            msg = re.sub(r'\b\d{4,}\b', '[NUMBER REDACTED]', msg)
            msg = re.sub(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL REDACTED]', msg)
            print('Login rejected. API code:', login.get('code'))
            print('Broker message (sanitized):', msg[:250] or '(empty)')
            print('Stopped; no further requests will be made.')
            return
        token = (login.get('data') or {}).get('token')
        if not token:
            print('Login returned code=0 but no data.token; stopped safely.')
            return
        print('PASS: login succeeded; token received (not printed or saved).')
        print('2/2 Requesting US stock assets (exchangeType=5)...')
        assets = signed_post(session, ASSETS, {'exchangeType': 5}, channel, private, token, x_type=a.x_type)
        if assets.get('code') != 0:
            print('Asset query rejected. API code:', assets.get('code'))
            msg = str(assets.get('msg') or assets.get('message') or '')
            msg = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]+', r'\1[REDACTED]', msg)
            msg = re.sub(r'\b\d{4,}\b', '[NUMBER REDACTED]', msg)
            msg = re.sub(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL REDACTED]', msg)
            print('Broker message (sanitized):', msg[:250] or '(empty)')
            return
        data = assets.get('data') or {}
        if not isinstance(data, dict):
            print('Response data has unexpected structure; stopped without printing full response.')
            return
        print('PASS: asset query succeeded.')
        for key, title in [('asset','Total Assets'), ('enableBalance','Available Cash'), ('frozenBalance','Frozen Cash')]:
            print(f'{title}: {data.get(key, "not returned")}')
        holdings = data.get('stockHoldingList')
        print('Number of holdings:', len(holdings) if isinstance(holdings,list) else 'not returned')
        print('Values are as returned by uSMART; check currency/account in the broker app.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, requests.RequestException) as e:
        print('STOPPED SAFELY:', str(e).splitlines()[0][:200], file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nCancelled without placing any orders.', file=sys.stderr)
        sys.exit(130)