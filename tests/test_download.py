import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

import download


class TestParseHttpDate:
    def test_valid_date(self):
        ts = download.parse_http_date("Sat, 30 May 2026 12:00:00 GMT")
        assert ts > 0

    def test_empty_returns_zero(self):
        assert download.parse_http_date("") == 0

    def test_invalid_returns_zero(self):
        assert download.parse_http_date("not-a-date") == 0


class TestFetchCookie:
    def test_success(self):
        session = MagicMock()
        response = MagicMock()
        response.headers = {"Set-Cookie": "dbsession=abc123; path=/; expires=..."}
        session.get.return_value = response
        cookie = download.fetch_cookie(session)
        assert cookie == "dbsession=abc123"
        session.get.assert_called_once_with("https://iceportal.de", allow_redirects=True)

    def test_no_cookie_raises(self):
        session = MagicMock()
        response = MagicMock()
        response.headers = {}
        session.get.return_value = response
        with pytest.raises(RuntimeError, match="No Set-Cookie"):
            download.fetch_cookie(session)


class TestResolveResumeState:
    def test_new_file(self):
        head = MagicMock()
        head.headers = {"Content-Length": "1000"}
        size, decoded, mode, range_h = download._resolve_resume_state(head, "/tmp/nonexistent")
        assert size == 1000
        assert decoded == 0
        assert mode == "wb"
        assert range_h is None

    def test_resume_supported(self, tmp_path):
        f = tmp_path / "partial.bin"
        f.write_bytes(b"a" * 500)
        head = MagicMock()
        head.headers = {"Content-Length": "1000", "Accept-Ranges": "bytes"}
        size, decoded, mode, range_h = download._resolve_resume_state(head, str(f))
        assert size == 1000
        assert decoded == 500
        assert mode == "ab"
        assert range_h == {"Range": "bytes=500-"}

    def test_resume_not_supported(self, tmp_path):
        f = tmp_path / "partial.bin"
        f.write_bytes(b"a" * 500)
        head = MagicMock()
        head.headers = {"Content-Length": "1000"}
        size, decoded, mode, range_h = download._resolve_resume_state(head, str(f))
        assert decoded == 0
        assert mode == "wb"
        assert range_h is None

    def test_already_complete(self, tmp_path):
        f = tmp_path / "complete.bin"
        f.write_bytes(b"a" * 1000)
        head = MagicMock()
        head.headers = {"Content-Length": "1000", "Accept-Ranges": "bytes"}
        size, decoded, mode, range_h = download._resolve_resume_state(head, str(f))
        assert decoded == 0
        assert mode == "wb"
        assert range_h is None


class TestIsAudiobookPresent:
    def test_exists(self):
        with patch("download.Path") as MockPath:
            mock = MagicMock()
            mock.exists.return_value = True
            MockPath.return_value = mock
            assert download.is_audiobook_present("/page/test") is True
            MockPath.assert_called_once_with("data/page/test/done")

    def test_missing(self):
        with patch("download.Path") as MockPath:
            mock = MagicMock()
            mock.exists.return_value = False
            MockPath.return_value = mock
            assert download.is_audiobook_present("/page/test") is False


class TestSaveAudiobookMetadata:
    def test_writes_files(self, tmp_path):
        d = tmp_path / "audiobook"
        d.mkdir()
        download._save_audiobook_metadata(d, '{"title": "Test"}')
        assert (d / "page.json").read_text() == '{"title": "Test"}'
        assert (d / "working").exists()


class TestDownloadFile:
    def test_already_downloaded_same_size(self, tmp_path, capsys):
        f = tmp_path / "file.bin"
        f.write_bytes(b"x" * 100)
        session = MagicMock()
        head = MagicMock()
        head.headers = {"Content-Length": "100", "Last-Modified": "Sat, 30 May 2026 12:00:00 GMT"}
        session.head.return_value = head

        download.download_file("http://example.com/file", str(f), session)
        captured = capsys.readouterr()
        assert "already fully downloaded" in captured.out
        session.get.assert_not_called()

    def test_no_content_length_simple_download(self, tmp_path):
        f = tmp_path / "file.bin"
        session = MagicMock()
        head = MagicMock()
        head.headers = {"Last-Modified": "Sat, 30 May 2026 12:00:00 GMT"}
        session.head.return_value = head

        response = MagicMock()
        response.iter_content.return_value = [b"chunk1", b"chunk2"]
        session.get.return_value.__enter__ = MagicMock(return_value=response)
        session.get.return_value.__exit__ = MagicMock(return_value=False)

        download.download_file("http://example.com/file", str(f), session)
        assert f.read_bytes() == b"chunk1chunk2"

    def test_missing_last_modified_raises(self):
        session = MagicMock()
        head = MagicMock()
        head.headers = {}
        session.head.return_value = head
        with pytest.raises(RuntimeError, match="Last-Modified header not found"):
            download.download_file("http://example.com/file", "/tmp/file", session)


class TestDownloadEpisode:
    def test_success(self):
        session = MagicMock()
        lr = MagicMock()
        lr.json.return_value = {"path": "/cdn/file.m4a"}
        session.get.return_value = lr

        with patch("download.download_file") as mock_dl:
            download._download_episode(
                {"path": "audiobook/path/test/1"}, "data/page", "https://iceportal.de", session
            )
            mock_dl.assert_called_once()
            args = mock_dl.call_args[0]
            assert args[0] == "https://iceportal.de/cdn/file.m4a"
            assert "audiobook/path/test/1" in args[1]

    def test_no_path_skips(self):
        session = MagicMock()
        with patch("download.download_file") as mock_dl:
            download._download_episode({}, "data/page", "https://iceportal.de", session)
            mock_dl.assert_not_called()


class TestDownloadAudiobook:
    def test_non_200_status(self, capsys):
        session = MagicMock()
        session.get.return_value.status_code = 404
        download.download_audiobook("/page/test", session)
        captured = capsys.readouterr()
        assert "HTTP 404" in captured.out

    def test_success(self, tmp_path):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.text = '{"files": [{"path": "audiobook/path/test/1"}]}'
        response.json.return_value = {"files": [{"path": "audiobook/path/test/1"}]}
        session.get.return_value = response

        with patch("download._save_audiobook_metadata"), \
             patch("download._download_episode") as mock_ep:
            download.download_audiobook("/page/test", session)
            mock_ep.assert_called_once()


class TestFetchAudiobookList:
    def test_success(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"teaserGroups": [{"items": [{"title": "Book"}]}]}
        session.get.return_value = response
        items = download.fetch_audiobook_list(session)
        assert items == [{"title": "Book"}]

    def test_error_returns_empty(self):
        session = MagicMock()
        session.get.side_effect = requests.RequestException("network")
        items = download.fetch_audiobook_list(session)
        assert items == []


class TestParseArgs:
    def test_default(self):
        with patch.object(sys, "argv", ["download.py"]):
            args = download._parse_args()
            assert args.list is False
            assert args.filter == ""

    def test_list_flag(self):
        with patch.object(sys, "argv", ["download.py", "--list"]):
            args = download._parse_args()
            assert args.list is True

    def test_filter_value(self):
        with patch.object(sys, "argv", ["download.py", "--filter", "Sylt"]):
            args = download._parse_args()
            assert args.filter == "Sylt"


class TestListAudiobooks:
    def test_output(self, capsys):
        with patch("download.is_audiobook_present", return_value=True):
            download._list_audiobooks([
                {"navigation": {"linktext": "Book A", "href": "/a"}}
            ])
        captured = capsys.readouterr()
        assert "Book A (downloaded)" in captured.out

    def test_not_downloaded(self, capsys):
        with patch("download.is_audiobook_present", return_value=False):
            download._list_audiobooks([
                {"navigation": {"linktext": "Book B", "href": "/b"}}
            ])
        captured = capsys.readouterr()
        assert "Book B (not downloaded)" in captured.out


class TestDownloadAll:
    def test_downloads_present(self):
        session = MagicMock()
        with patch("download.is_audiobook_present", return_value=False), \
             patch("download.download_audiobook") as mock_dl:
            download._download_all([
                {"navigation": {"linktext": "Book", "href": "/page/book"}}
            ], session)
            mock_dl.assert_called_once_with("/page/book", session)

    def test_skips_no_href(self, capsys):
        session = MagicMock()
        with patch("download.is_audiobook_present", return_value=False), \
             patch("download.download_audiobook") as mock_dl:
            download._download_all([
                {"navigation": {"linktext": "NoHref", "href": ""}}
            ], session)
        mock_dl.assert_not_called()
        captured = capsys.readouterr()
        assert "Skipping item with no href" in captured.out
