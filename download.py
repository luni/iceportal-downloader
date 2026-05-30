#!/usr/bin/env python3
import argparse
import os
import time
from pathlib import Path

import requests
from tqdm import tqdm


def fetch_cookie(session: requests.Session) -> str:
    """Fetch a session cookie from the main page."""
    resp = session.get("https://iceportal.de", allow_redirects=True)
    resp.raise_for_status()
    cookie = resp.headers.get("Set-Cookie", "")
    if not cookie:
        raise RuntimeError("No Set-Cookie header received from iceportal.de")
    return cookie.split(";")[0]


def parse_http_date(date_str: str) -> float:
    """Parse HTTP date string to timestamp."""
    from email.utils import parsedate_to_datetime
    if not date_str:
        return 0
    try:
        return parsedate_to_datetime(date_str).timestamp()
    except (TypeError, ValueError):
        return 0

def _resolve_resume_state(
    head_response: requests.Response, local_filepath: str
) -> tuple[int, int, str, dict[str, str] | None]:
    """Return (total_size, decoded_bytes, mode, range_header) for a resumable download."""
    total_size = int(head_response.headers['Content-Length'])
    decoded_bytes = 0

    if os.path.exists(local_filepath):
        decoded_bytes = os.path.getsize(local_filepath)
        if decoded_bytes >= total_size:
            return total_size, 0, 'wb', None  # signal: already done

    mode = 'ab' if decoded_bytes > 0 else 'wb'
    range_header = None

    if decoded_bytes > 0:
        if head_response.headers.get('Accept-Ranges') != 'bytes':
            print("Server does not support byte ranges, downloading from the beginning...")
            decoded_bytes = 0
            mode = 'wb'
        else:
            range_header = {"Range": f"bytes={decoded_bytes}-"}

    return total_size, decoded_bytes, mode, range_header


def _download_simple(
    url: str, local_filepath: str, session: requests.Session, remote_mtime: float
) -> None:
    """Download a file when Content-Length is not available."""
    with session.get(url, stream=True) as response:
        response.raise_for_status()
        os.makedirs(os.path.dirname(os.path.abspath(local_filepath)), exist_ok=True)
        with open(local_filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
    if remote_mtime:
        os.utime(local_filepath, (remote_mtime, remote_mtime))


def _download_chunked(
    url: str,
    local_filepath: str,
    session: requests.Session,
    total_size: int,
    decoded_bytes: int,
    mode: str,
    range_header: dict[str, str] | None,
    remote_mtime: float,
) -> None:
    """Download a file with a known size, optionally resuming."""
    with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024) as pbar:
        pbar.update(decoded_bytes)
        with session.get(url, stream=True, headers=range_header) as response:
            response.raise_for_status()
            os.makedirs(os.path.dirname(os.path.abspath(local_filepath)), exist_ok=True)
            with open(local_filepath, mode) as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
    if remote_mtime:
        os.utime(local_filepath, (remote_mtime, remote_mtime))


def download_file(url: str, local_filepath: str, session: requests.Session) -> None:
    """Download a single file with resume support."""
    head_response = session.head(url)
    head_response.raise_for_status()

    last_modified = head_response.headers.get('Last-Modified')
    if not last_modified:
        raise RuntimeError(f"Last-Modified header not found for {url}")
    remote_mtime = parse_http_date(last_modified)

    file_exists = os.path.exists(local_filepath)
    if file_exists and 'Content-Length' not in head_response.headers:
        print(f"File {local_filepath} exists and no Content-Length available - skipping")
        return

    if file_exists and 'Content-Length' in head_response.headers:
        if os.path.getsize(local_filepath) == int(head_response.headers['Content-Length']):
            print(f"File {local_filepath} is already fully downloaded")
            return

    if 'Content-Length' not in head_response.headers:
        print(f"Content-Length not available, downloading {url}...")
        _download_simple(url, local_filepath, session, remote_mtime)
        return

    total_size, decoded_bytes, mode, range_header = _resolve_resume_state(
        head_response, local_filepath
    )
    if decoded_bytes == 0 and mode == 'wb' and os.path.exists(local_filepath):
        # Signal from _resolve_resume_state: file is already complete
        return

    _download_chunked(url, local_filepath, session, total_size, decoded_bytes, mode, range_header, remote_mtime)


def _save_audiobook_metadata(data_dir: Path, response_text: str) -> None:
    """Write working marker and page.json."""
    (data_dir / "working").write_text(str(time.time()))
    (data_dir / "page.json").write_text(response_text, encoding='utf-8')


def _download_episode(
    src_file: dict, data_path: str, base_url: str, session: requests.Session
) -> None:
    """Resolve and download a single episode file."""
    path = src_file.get("path")
    if not path:
        return

    file_path = Path(f"{data_path}{path}")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    local_path = file_path.resolve()

    try:
        lr = session.get(f"{base_url}/api1/rs/{path}")
        lr.raise_for_status()
        file_url = lr.json().get("path")
        if file_url:
            download_file(f"{base_url}{file_url}", str(local_path), session)
    except (requests.RequestException, ValueError) as e:
        print(f"Error processing {path}: {e}")


def download_audiobook(page: str, session: requests.Session) -> None:
    """Download an audiobook from ICE Portal."""
    base_url = "https://iceportal.de"
    data_path = f"data{page}"

    r = session.get(f"{base_url}/api1/rs/page{page}/")
    if r.status_code != 200:
        print(f"HTTP {r.status_code} error accessing {base_url}/api1/rs/page{page}/")
        return

    data_dir = Path(data_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    _save_audiobook_metadata(data_dir, r.text)

    for src_file in r.json().get("files", []):
        _download_episode(src_file, data_path, base_url, session)

    done_file = data_dir / "done"
    done_file.unlink(missing_ok=True)
    done_file.write_text(str(time.time()))


def is_audiobook_present(page: str) -> bool:
    """Check if an audiobook has already been downloaded."""
    return Path(f"data{page}/done").exists()


def fetch_audiobook_list(session: requests.Session) -> list[dict]:
    """Fetch and return the audiobook list from ICE Portal."""
    try:
        response = session.get("https://iceportal.de/api1/rs/page/hoerbuecher")
        response.raise_for_status()
        return response.json()["teaserGroups"][0]["items"]
    except (requests.RequestException, KeyError) as e:
        print(f"Error fetching audiobook list: {e}")
        return []


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="ICE Portal audiobook downloader")
    parser.add_argument("--list", action="store_true", help="List available audiobooks")
    parser.add_argument("--filter", type=str, default="", help="Filter audiobooks by name (case-insensitive)")
    return parser.parse_args()


def _list_audiobooks(items: list[dict]) -> None:
    """Print available audiobooks and their download status."""
    for item in items:
        nav = item.get("navigation", {})
        title = nav.get("linktext", "Untitled")
        href = nav.get("href", "")
        status = "downloaded" if is_audiobook_present(href) else "not downloaded"
        print(f"  {title} ({status})")


def _download_all(items: list[dict], session: requests.Session) -> None:
    """Download all audiobooks in the given list."""
    for item in items:
        nav = item.get("navigation", {})
        if not nav:
            continue

        title = nav.get("linktext", "Untitled")
        href = nav.get("href", "")

        if not href:
            print(f"Skipping item with no href: {title}")
            continue

        print(f"\nProcessing: {title}")
        if not is_audiobook_present(href):
            print("Starting download...")
            download_audiobook(href, session)
        else:
            print("Already downloaded - skipping")


def main() -> None:
    """Main entry point for the ICE Portal downloader."""
    args = _parse_args()

    session = requests.Session()
    try:
        cookie = fetch_cookie(session)
        session.headers["Cookie"] = cookie
    except (requests.RequestException, RuntimeError) as e:
        print(f"Error fetching cookie: {e}")
        return

    hoerbuecher = fetch_audiobook_list(session)
    if not hoerbuecher:
        return

    if args.filter:
        hoerbuecher = [
            item for item in hoerbuecher
            if args.filter.lower() in item.get("navigation", {}).get("linktext", "").lower()
        ]

    if args.list:
        _list_audiobooks(hoerbuecher)
        return

    _download_all(hoerbuecher, session)


if __name__ == "__main__":
    main()
