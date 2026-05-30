#!/usr/bin/env python3
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

def download_file(url: str, local_filepath: str, session: requests.Session):
    chunk_size = 1024  # 1kb

    # First make a HEAD request to check headers
    head_response = session.head(url)
    head_response.raise_for_status()

    # Get Last-Modified header if available
    last_modified = head_response.headers.get('Last-Modified')
    if not last_modified:
        raise RuntimeError(f"Last-Modified header not found for {url}")

    remote_mtime = parse_http_date(last_modified)

    # Check if file exists and should be skipped
    file_exists = os.path.exists(local_filepath)
    if file_exists:
        # Skip if no Content-Length header (we can't verify the download)
        if 'Content-Length' not in head_response.headers:
            print(f"File {local_filepath} exists and no Content-Length available - skipping")
            return

        # Skip if file size matches Content-Length
        file_size = os.path.getsize(local_filepath)
        if file_size == int(head_response.headers['Content-Length']):
            print(f"File {local_filepath} is already fully downloaded")
            return

    # If Content-Length is not present, we'll do a simple download
    if 'Content-Length' not in head_response.headers:
        print(f"Content-Length not available, downloading {url}...")
        with session.get(url, stream=True) as response:
            response.raise_for_status()
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(os.path.abspath(local_filepath)), exist_ok=True)
            with open(local_filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
            # Set the modification time if Last-Modified header was provided
            if remote_mtime:
                os.utime(local_filepath, (remote_mtime, remote_mtime))
        return

    # Original download logic for files with Content-Length
    total_size = int(head_response.headers['Content-Length'])
    decoded_bytes_downloaded = 0

    if file_exists:
        decoded_bytes_downloaded = os.path.getsize(local_filepath)
        if decoded_bytes_downloaded >= total_size:
            return

    range_header = None
    mode = 'ab' if decoded_bytes_downloaded > 0 else 'wb'

    if decoded_bytes_downloaded > 0:
        if head_response.headers.get('Accept-Ranges') != 'bytes':
            print("Server does not support byte ranges, downloading from the beginning...")
            decoded_bytes_downloaded = 0
            mode = 'wb'
        else:
            range_header = {"Range": f"bytes={decoded_bytes_downloaded}-"}

    with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024) as pbar:
        pbar.update(decoded_bytes_downloaded)
        with session.get(url, stream=True, headers=range_header) as response:
            response.raise_for_status()
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(os.path.abspath(local_filepath)), exist_ok=True)
            with open(local_filepath, mode) as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
                        pbar.update(len(chunk))

        # Set the modification time if Last-Modified header was provided
        if remote_mtime:
            os.utime(local_filepath, (remote_mtime, remote_mtime))


def download_audiobook(page: str, session: requests.Session) -> None:
    """Download an audiobook from ICE Portal.

    Args:
        page: The page path for the audiobook
        session: Requests session with authentication cookie
    """
    base_url = "https://iceportal.de"
    api_base_path = "/api1/rs/page"
    data_base_path = "data"

    api_base_url = f"{base_url}{api_base_path}"
    data_path = f"{data_base_path}{page}"

    r = session.get(f"{api_base_url}{page}/")

    if r.status_code != 200:
        print(f"HTTP {r.status_code} error accessing {api_base_url}{page}")
        return

    # Create data directory and save metadata
    data_dir = Path(data_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Save working timestamp
    working_file = data_dir / "working"
    working_file.write_text(str(time.time()))

    # Save page data
    page_file = data_dir / "page.json"
    page_file.write_text(r.text, encoding='utf-8')

    data = r.json()
    files = data.get("files", [])

    for src_file in files:
        path = src_file.get("path")
        if not path:
            continue

        file_path = Path(f"{data_path}{path}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        local_path = file_path.resolve()

        # Get the actual file URL
        path_api_url = f"{base_url}/api1/rs/{path}"
        try:
            lr = session.get(path_api_url)
            lr.raise_for_status()
            file_url = lr.json().get("path")
            if file_url:
                download_file(f"{base_url}{file_url}", str(local_path), session)
        except (requests.RequestException, ValueError) as e:
            print(f"Error processing {path}: {e}")
            continue

    # Mark download as complete
    done_file = data_dir / "done"
    done_file.unlink(missing_ok=True)
    done_file.write_text(str(time.time()))


def is_audiobook_present(page: str) -> bool:
    """Check if an audiobook has already been downloaded.

    Args:
        page: The page path for the audiobook

    Returns:
        bool: True if the audiobook is already downloaded, False otherwise
    """
    data_path = f"data{page}"
    done_path = Path(f"{data_path}/done")
    working_path = Path(f"{data_path}/working")

    # Currently disabled - always return False to force re-check
    return False


def main() -> None:
    """Main entry point for the ICE Portal downloader."""
    session = requests.Session()
    try:
        cookie = fetch_cookie(session)
        session.headers["Cookie"] = cookie
    except (requests.RequestException, RuntimeError) as e:
        print(f"Error fetching cookie: {e}")
        return

    try:
        response = session.get("https://iceportal.de/api1/rs/page/hoerbuecher")
        response.raise_for_status()
        hoerbuecher = response.json()["teaserGroups"][0]["items"]
    except (requests.RequestException, KeyError) as e:
        print(f"Error fetching audiobook list: {e}")
        return

    for item in hoerbuecher:
        nav = item.get("navigation", {})
        if not nav:
            continue

        title = nav.get("linktext", "Untitled")
        href = nav.get("href", "")

        if not href:
            print(f"Skipping item with no href: {title}")
            continue

        # Skip podcasts if needed
        # if '/pc_' in href:
        #     continue

        print(f"\nProcessing: {title}")
        if not is_audiobook_present(href):
            print("Starting download...")
            download_audiobook(href, session)
        else:
            print("Already downloaded - skipping")


if __name__ == "__main__":
    main()
