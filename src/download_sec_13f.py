#!/usr/bin/env python3
"""Download official SEC Form 13F ZIP datasets (default: 2023–2026).

No uSMART credentials, trading requests, or synthetic data. SEC may block some networks.
Usage:
  python3 -m pip install requests
  python3 download_sec_13f.py --email your-contact@example.com
  python3 download_sec_13f.py --email your-contact@example.com --years 2024 2025 2026
  python3 download_sec_13f.py --email your-contact@example.com --list-only

A real, reachable contact email is required in the SEC User-Agent (not your broker login).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

SOURCE = 'https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets'
DEFAULT_DIR = Path.home() / 'Documents' / 'usmart' / 'sec_13f_zips'

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.parts = []
    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            self.links.append((' '.join(''.join(self.parts).split()), self.href))
            self.href = None
            self.parts = []

def discover(session: requests.Session, years: set[int]) -> list[tuple[str,str]]:
    try:
        res = session.get(SOURCE, timeout=35)
        res.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f'Cannot fetch SEC download index: {e}. Check your network or SEC access policy.') from e
    parser = LinkParser()
    parser.feed(res.text)
    output = []
    for title, href in parser.links:
        url = urljoin(SOURCE, href)
        if not urlparse(url).hostname in ('sec.gov', 'www.sec.gov'):
            continue
        if not urlparse(url).path.lower().endswith('.zip') or 'form13f' not in url.lower():
            continue
        years_in_title = {int(y) for y in re.findall(r'\b20\d{2}\b', title)}
        if not years.intersection(years_in_title):
            continue
        output.append((title, url))
    unique = dict((url, title) for title, url in output)
    return [(title, url) for url, title in unique.items()]

def valid_zip(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None and bool(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False

def download(session: requests.Session, url: str, dest: Path):
    temp = dest.with_name(dest.name + '.part')
    for attempt in range(1, 4):
        try:
            with session.get(url, stream=True, timeout=(20, 180)) as response:
                if response.status_code in (403, 401):
                    raise RuntimeError(f'SEC returned HTTP {response.status_code}. Stop and use the official website manually; do not retry aggressively.')
                if response.status_code == 429:
                    raise RuntimeError('SEC rate limit HTTP 429. Stop, wait, and retry later.')
                response.raise_for_status()
                ctype = response.headers.get('Content-Type', '').lower()
                if 'text/html' in ctype:
                    raise RuntimeError('SEC returned HTML instead of a ZIP (possibly blocked).')
                with temp.open('wb') as f:
                    for chunk in response.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
            if not valid_zip(temp):
                raise RuntimeError('Downloaded file failed ZIP integrity check.')
            temp.replace(dest)
            return
        except (requests.RequestException, OSError, RuntimeError) as exc:
            temp.unlink(missing_ok=True)
            if attempt == 3 or isinstance(exc, RuntimeError):
                raise RuntimeError(str(exc)) from exc
            time.sleep(3 * attempt)

def main() -> int:
    p = argparse.ArgumentParser(description='Download public SEC Form 13F quarterly archives, with ZIP validation.')
    p.add_argument('--email', required=True, help='Your real contact email for SEC User-Agent; not a broker password')
    p.add_argument('--years', nargs='+', type=int, default=[2023,2024,2025,2026])
    p.add_argument('--output', type=Path, default=DEFAULT_DIR)
    p.add_argument('--list-only', action='store_true')
    args = p.parse_args()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', args.email):
        p.error('--email must be a valid contact email address')
    session = requests.Session()
    session.headers.update({'User-Agent': f'PopQuants Research Researcher {args.email}',
                            'Accept-Encoding': 'gzip, deflate',
                            'Accept': 'text/html,application/zip,*/*'})
    print('Official SEC source:', SOURCE)
    print('Selected years:', ', '.join(map(str, args.years)))
    items = discover(session, set(args.years))
    if not items:
        print('ERROR: No matching ZIP links found on SEC index. Page structure may have changed.', file=sys.stderr)
        return 1
    print(f'Found {len(items)} archives:')
    for title, url in items:
        print(' -', title, '|', url.rsplit('/',1)[-1])
    if args.list_only:
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    print('Destination:', args.output.resolve())
    completed = skipped = 0
    for idx, (title, url) in enumerate(items, 1):
        name = urlparse(url).path.rsplit('/',1)[-1]
        dest = args.output / name
        if valid_zip(dest):
            skipped += 1
            print(f'[{idx}/{len(items)}] SKIP valid existing: {name}')
            continue
        print(f'[{idx}/{len(items)}] GET {name} ...', flush=True)
        try:
            download(session, url, dest)
            completed += 1
            print(f'  OK {dest.stat().st_size/1024/1024:.1f} MiB; ZIP integrity verified')
        except RuntimeError as e:
            print('STOP:', e, file=sys.stderr)
            print(f'Saved {completed} new files; {skipped} already existed. Safe to rerun later.', file=sys.stderr)
            return 1
        time.sleep(1.5)
    print(f'DONE: downloaded {completed}, skipped {skipped}; total {len(items)} valid archives')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())