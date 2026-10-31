.PHONY: test lint run build smoke help

help:
	@echo "Available commands:"
	@echo "  make test   - Run test suite with pytest"
	@echo "  make lint   - Run ruff check"
	@echo "  make run    - Run development server with gunicorn/gevent"
	@echo "  make build  - Display multi-arch docker buildx commands"
	@echo "  make smoke  - Run yt-dlp smoke test script"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .

run:
	.venv/bin/python3 -m gunicorn --worker-class gevent --workers 1 --bind 0.0.0.0:5000 app:app

build:
	@echo "Multi-arch Docker buildx command (SPEC 6.1):"
	@echo "  docker buildx build --platform linux/amd64,linux/arm64 -t jungle-downloader:latest ."
	@echo ""
	@echo "To load into local Docker daemon (single arch):"
	@echo "  docker buildx build --load -t jungle-downloader:latest ."

smoke:
	.venv/bin/python3 scripts/smoke_test.py
