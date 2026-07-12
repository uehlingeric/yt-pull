.PHONY: setup lint run clean

setup:
	uv sync

lint:
	uv run ruff check fetch_transcript.py
	uv run ruff format --check fetch_transcript.py

# usage: make run VIDEO=dQw4w9WgXcQ
run:
	uv run fetch_transcript.py $(VIDEO) --output-dir ./output

clean:
	rm -rf .venv output __pycache__
