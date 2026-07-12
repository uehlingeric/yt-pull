#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "youtube-transcript-api>=0.6.0",
#     "yt-dlp>=2024.1.0",
# ]
# ///
"""
fetch_transcript.py — YouTube transcript fetcher for yt-pull skill.

Usage:
    # Single video
    uv run fetch_transcript.py <video_id> [--lang en] [--output-dir ./out]

    # List videos from a channel
    uv run fetch_transcript.py --channel <channel_name_or_url> [--max-videos 10] [--output-dir ./out]

Outputs (single video):
    transcript.txt   — clean plain text
    transcript.json  — structured [{text, start, duration}, ...]
    metadata.json    — video title, channel, date, duration, views, description

Outputs (channel mode):
    channel.json     — list of [{id, title, upload_date, duration_string, view_count}, ...]

Primary method: youtube-transcript-api (lightweight, returns structured data)
Fallback method: yt-dlp (heavier, needs VTT cleaning, but more robust)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Primary method: youtube-transcript-api
# ---------------------------------------------------------------------------

def fetch_with_transcript_api(video_id: str, lang: str) -> dict | None:
    """
    Returns {"entries": [{text, start, duration}, ...], "source": "api"} or None.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=[lang])

        entries = []
        for entry in transcript:
            entries.append({
                "text": str(entry.text).strip(),
                "start": float(entry.start),
                "duration": float(entry.duration),
            })

        return {"entries": entries, "source": "youtube-transcript-api"}
    except Exception as e:
        print(f"[youtube-transcript-api] Failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Fallback method: yt-dlp
# ---------------------------------------------------------------------------

def fetch_with_ytdlp(video_id: str, lang: str, tmp_dir: str) -> dict | None:
    """
    Returns {"entries": [{text, start, duration}, ...], "source": "yt-dlp"} or None.
    """
    # Prefer the yt-dlp executable; fall back to running it as a python module
    ytdlp = shutil.which("yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = os.path.join(tmp_dir, "%(id)s")

    # Try auto-subs first, then manual subs
    for sub_flag in ["--write-auto-sub", "--write-sub"]:
        cmd = [
            ytdlp or sys.executable, *([] if ytdlp else ["-m", "yt_dlp"]),
            sub_flag, "--sub-lang", lang,
            "--skip-download", "--sub-format", "vtt",
            "-o", out_template, url,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue

        # Find the VTT file
        for f in Path(tmp_dir).glob(f"{video_id}*.vtt"):
            entries = parse_vtt(f.read_text())
            if entries:
                return {"entries": entries, "source": "yt-dlp (auto-subs)" if "auto" in sub_flag else "yt-dlp (manual-subs)"}

    return None


def parse_vtt(vtt_text: str) -> list[dict]:
    """Parse VTT content into structured entries with timestamps."""
    entries = []
    lines = vtt_text.split("\n")
    current_start = None
    current_duration = 0.0
    current_text_lines = []

    for line in lines:
        # Skip header lines
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:") or line.startswith("NOTE"):
            continue

        # Timestamp line
        ts_match = re.match(r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})', line)
        if ts_match:
            # Save previous entry
            if current_start is not None and current_text_lines:
                text = clean_subtitle_text(" ".join(current_text_lines))
                if text:
                    entries.append({"text": text, "start": current_start, "duration": current_duration})

            current_start = parse_timestamp(ts_match.group(1))
            end = parse_timestamp(ts_match.group(2))
            current_duration = round(end - current_start, 3)
            current_text_lines = []
            continue

        # Skip position/alignment lines
        if re.match(r'^(align|position|line|size):', line.strip()):
            continue

        # Content line
        stripped = line.strip()
        if stripped:
            current_text_lines.append(stripped)

    # Save last entry
    if current_start is not None and current_text_lines:
        text = clean_subtitle_text(" ".join(current_text_lines))
        if text:
            entries.append({"text": text, "start": current_start, "duration": current_duration})

    return entries


def parse_timestamp(ts: str) -> float:
    """Convert HH:MM:SS.mmm to seconds."""
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def clean_subtitle_text(text: str) -> str:
    """Strip HTML tags, inline timestamps, and normalize whitespace."""
    text = re.sub(r'<[^>]+>', '', text)          # HTML tags
    text = re.sub(r'\s+', ' ', text).strip()      # collapse whitespace
    return text


# ---------------------------------------------------------------------------
# Metadata via yt-dlp
# ---------------------------------------------------------------------------

def fetch_metadata(video_id: str) -> dict | None:
    """Fetch video metadata via yt-dlp --dump-json."""
    ytdlp = shutil.which("yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"

    cmd = [
        ytdlp or sys.executable, *([] if ytdlp else ["-m", "yt_dlp"]),
        "--dump-json", "--skip-download", url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, text=True, check=True)
        d = json.loads(result.stdout)
        return {
            "title": d.get("title", ""),
            "channel": d.get("channel", d.get("uploader", "")),
            "upload_date": d.get("upload_date", ""),
            "duration_string": d.get("duration_string", ""),
            "description": d.get("description", "")[:500],
            "view_count": d.get("view_count", 0),
        }
    except Exception as e:
        print(f"[metadata] Failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Text assembly
# ---------------------------------------------------------------------------

def entries_to_plain_text(entries: list[dict]) -> str:
    """Convert structured entries to clean, deduplicated plain text with paragraph breaks."""
    seen_texts = []
    for e in entries:
        text = e["text"]
        # Deduplicate consecutive identical lines
        if not seen_texts or text != seen_texts[-1]:
            seen_texts.append(text)

    full_text = " ".join(seen_texts)
    full_text = re.sub(r'\s+', ' ', full_text).strip()

    # Break into paragraphs every ~4 sentences
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    paragraphs = []
    current = []
    for s in sentences:
        current.append(s)
        if len(current) >= 4:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Channel listing
# ---------------------------------------------------------------------------

def resolve_channel_url(channel_input: str) -> str:
    """Resolve a channel name/handle/URL to a yt-dlp-compatible URL."""
    # Already a full URL
    if channel_input.startswith("http"):
        # Ensure it ends with /videos for listing
        url = channel_input.rstrip("/")
        if not url.endswith("/videos"):
            url += "/videos"
        return url

    # Handle with @ prefix
    name = channel_input.lstrip("@")
    return f"https://www.youtube.com/@{name}/videos"


def list_channel_videos(channel_input: str, max_videos: int = 10) -> list[dict] | None:
    """
    List recent videos from a YouTube channel.
    Returns [{id, title, upload_date, duration_string, view_count, url}, ...] or None.
    """
    ytdlp = shutil.which("yt-dlp")

    channel_url = resolve_channel_url(channel_input)
    print(f"Listing up to {max_videos} videos from {channel_url}...", file=sys.stderr)

    cmd = [
        ytdlp or sys.executable, *([] if ytdlp else ["-m", "yt_dlp"]),
        "--flat-playlist", "--dump-json",
        "--playlist-end", str(max_videos),
        channel_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[channel] Failed to list videos: {e}", file=sys.stderr)
        return None

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            videos.append({
                "id": d.get("id", ""),
                "title": d.get("title", ""),
                "upload_date": d.get("upload_date", ""),
                "duration_string": d.get("duration_string", d.get("duration", "")),
                "view_count": d.get("view_count", 0),
                "url": d.get("url", f"https://www.youtube.com/watch?v={d.get('id', '')}"),
            })
        except json.JSONDecodeError:
            continue

    return videos if videos else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube transcript or list channel videos")
    parser.add_argument("video_id", nargs="?", default=None, help="YouTube video ID (11 chars)")
    parser.add_argument("--channel", default=None, help="Channel name, @handle, or URL to list videos from")
    parser.add_argument("--max-videos", type=int, default=10, help="Max videos to list from channel (default: 10)")
    parser.add_argument("--lang", default="en", help="Language code (default: en)")
    parser.add_argument("--output-dir", default=".", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Channel mode: list videos ---
    if args.channel:
        videos = list_channel_videos(args.channel, args.max_videos)
        if not videos:
            print("ERROR: Could not list videos from channel.", file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(videos)} videos", file=sys.stderr)

        channel_path = output_dir / "channel.json"
        channel_path.write_text(json.dumps(videos, indent=2, ensure_ascii=False))
        print(f"Wrote {channel_path}", file=sys.stderr)

        # Print summary to stdout for Claude to read
        print(json.dumps(videos, indent=2, ensure_ascii=False))
        return

    # --- Single video mode ---
    if not args.video_id:
        parser.error("Either a video_id or --channel is required")

    video_id = args.video_id
    lang = args.lang

    tmp_dir = f"/tmp/yt-pull-{video_id}"
    os.makedirs(tmp_dir, exist_ok=True)

    # --- Fetch transcript ---
    print(f"Fetching transcript for {video_id} (lang={lang})...", file=sys.stderr)

    result = fetch_with_transcript_api(video_id, lang)
    if not result:
        print("Primary method failed, trying yt-dlp fallback...", file=sys.stderr)
        result = fetch_with_ytdlp(video_id, lang, tmp_dir)

    if not result:
        print("ERROR: Could not fetch transcript with any method.", file=sys.stderr)
        sys.exit(1)

    entries = result["entries"]
    source = result["source"]
    print(f"Got {len(entries)} entries via {source}", file=sys.stderr)

    # --- Fetch metadata ---
    print("Fetching metadata...", file=sys.stderr)
    metadata = fetch_metadata(video_id)

    # --- Write outputs ---
    plain_text = entries_to_plain_text(entries)

    txt_path = output_dir / "transcript.txt"
    txt_path.write_text(plain_text)
    print(f"Wrote {txt_path} ({len(plain_text)} chars, {len(plain_text.split())} words)", file=sys.stderr)

    json_path = output_dir / "transcript.json"
    json_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"Wrote {json_path} ({len(entries)} entries)", file=sys.stderr)

    if metadata:
        meta_path = output_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        print(f"Wrote {meta_path}", file=sys.stderr)

    # --- Print transcript to stdout for Claude to read ---
    print(plain_text)


if __name__ == "__main__":
    main()
