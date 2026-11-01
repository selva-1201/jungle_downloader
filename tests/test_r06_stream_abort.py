from unittest.mock import MagicMock, patch
import pytest
from app import stream_via_direct_url, stream_universal_ffmpeg
from concurrency import StreamAborted, ReleasingIterator, proxy_semaphore, ffmpeg_semaphore


def test_direct_stream_over_byte_cap_raises_stream_aborted(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "MAX_FILESIZE_MB", 1)  # 1 MB limit

    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Type": "video/mp4"}
    # Yield two 600KB chunks -> total 1.2MB > 1MB
    mock_resp.iter_content.return_value = [b"x" * (600 * 1024), b"y" * (600 * 1024)]

    with patch("app.safe_requests_get", return_value=mock_resp):
        fmt = {"url": "https://example.com/video.mp4", "ext": "mp4"}
        gen, _, _ = stream_via_direct_url(fmt, "video")

        permit_released = False
        def on_release():
            nonlocal permit_released
            permit_released = True

        wrapped = ReleasingIterator(gen, on_release=on_release)

        # First chunk ok
        chunk1 = next(wrapped)
        assert len(chunk1) == 600 * 1024
        assert not permit_released

        # Second chunk pushes over cap -> must raise StreamAborted
        with pytest.raises(StreamAborted):
            next(wrapped)

        # Permit must be released upon exception
        assert permit_released is True
        mock_resp.close.assert_called()


def test_ffmpeg_watchdog_kill_leads_to_abort(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "FFMPEG_TIMEOUT_SEC", 0.05)

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    # stdout.read returns 1 chunk then empty (simulating EOF after kill)
    mock_proc.stdout.read.side_effect = [b"valid_header", b""]

    # When proc.kill is called, set returncode
    def mock_kill():
        mock_proc.returncode = -9
    mock_proc.kill.side_effect = mock_kill

    with patch("app.gevent_subprocess.Popen", return_value=mock_proc):
        with patch("app.validate_public_url"):
            v_fmt = {"url": "https://example.com/v.mp4"}
            a_fmt = {"url": "https://example.com/a.m4a"}
            gen, _ = stream_universal_ffmpeg(v_fmt, a_fmt, is_audio_only=False)

            permit_released = False
            def on_release():
                nonlocal permit_released
                permit_released = True

            wrapped = ReleasingIterator(gen, on_release=on_release)

            # Read first chunk
            first = next(wrapped)
            assert first == b"valid_header"

            # Allow watchdog to fire
            import gevent
            gevent.sleep(0.08)

            # Second read should see watchdog killed -> raise StreamAborted, not EOF
            with pytest.raises(StreamAborted):
                next(wrapped)

            assert permit_released is True
