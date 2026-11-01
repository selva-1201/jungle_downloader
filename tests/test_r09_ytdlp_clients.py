from unittest.mock import patch, MagicMock
from app import fetch_fresh_metadata
from config import config
from security import get_safe_ytdlp_opts


def test_safe_ytdlp_opts_includes_js_runtimes():
    opts = get_safe_ytdlp_opts()
    assert "js_runtimes" in opts
    assert "node" in opts["js_runtimes"]


def test_default_youtube_opts_no_forced_player_client(monkeypatch):
    monkeypatch.setattr(config, "YT_PLAYER_CLIENTS", "")
    with patch("app.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"id": "123", "title": "test"}
        mock_instance.__enter__.return_value = mock_instance
        mock_ydl_cls.return_value = mock_instance

        fetch_fresh_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube")

        called_opts = mock_ydl_cls.call_args[0][0]
        # Should NOT contain forced player_client
        assert "extractor_args" not in called_opts or "player_client" not in called_opts.get("extractor_args", {}).get("youtube", {})


def test_youtube_opts_with_custom_player_clients(monkeypatch):
    monkeypatch.setattr(config, "YT_PLAYER_CLIENTS", "android, web")
    with patch("app.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"id": "123", "title": "test"}
        mock_instance.__enter__.return_value = mock_instance
        mock_ydl_cls.return_value = mock_instance

        fetch_fresh_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube")

        called_opts = mock_ydl_cls.call_args[0][0]
        assert "extractor_args" in called_opts
        assert called_opts["extractor_args"]["youtube"]["player_client"] == ["android", "web"]


def test_requirements_and_dockerfile_js_runtime():
    with open("requirements.txt", "r", encoding="utf-8") as f:
        reqs = f.read()
    assert "yt-dlp[default]" in reqs

    with open("Dockerfile", "r", encoding="utf-8") as f:
        dockerfile = f.read()
    assert "nodejs" in dockerfile
