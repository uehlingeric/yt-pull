# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [SemVer](https://semver.org/).

## [1.0.0] - 2026-07-11

### Added

- Transcript extraction for single videos (plain text, structured JSON, metadata) and channel listings
- Primary fetch via `youtube-transcript-api` with automatic `yt-dlp` VTT fallback
- Claude Code skill (`SKILL.md`) with three analysis depth levels

### Changed

- Dependencies now resolve through uv (PEP 723 inline script metadata plus `pyproject.toml` + `uv.lock`); removed ~90 lines of pip/venv self-bootstrap and the `/tmp/yt-pull-venv` re-exec path
