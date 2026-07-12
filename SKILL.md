---
name: yt-pull
description: Pull YouTube video transcripts from a URL, then provide a structured summary and analysis.
---

# YT Pull — YouTube Transcript Summary & Analysis

One command: paste a YouTube URL, get the transcript pulled and a full summary + analysis.

## CLI Interface

### Single video mode

```
/yt-pull <youtube-url> [options]
```

**Input:** A YouTube video URL (any format — full URL, short URL, or just a video ID).

Supported URL formats:
- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://youtube.com/watch?v=VIDEO_ID&t=123`
- `VIDEO_ID` (raw 11-character ID)

### Channel mode

```
/yt-pull --channel <channel> [options]
```

**Channel input** (any format):
- `@ChannelHandle` — e.g., `@nateherk`
- `ChannelHandle` — handle without `@`, same thing
- `https://www.youtube.com/@ChannelHandle` — full channel URL
- `https://www.youtube.com/c/ChannelName` — legacy channel URL

In channel mode, the skill first lists recent videos from the channel, then lets the user pick which ones to pull transcripts from (or pulls all of them).

**Options:**
| Flag | Values | Default |
|------|--------|---------|
| `--lang` | language code (e.g., `en`, `es`, `fr`) | `en` |
| `--output` | directory path for saved transcript | `./yt-pull` |
| `--depth` | `brief`, `standard`, `deep` | `standard` |
| `--max-videos` | max videos to list from channel | `10` |

Transcripts and metadata are always saved to the output directory.

## Output Directory Structure

### Single video
```
{output}/{video-slug}/
├── transcript.txt        # clean plain text
├── transcript.json       # structured [{text, start, duration}, ...]
├── metadata.json         # title, channel, date, duration, views
└── analysis.md           # summary & analysis (written in Step 4)
```

### Channel listing
```
{output}/{channel-slug}/
├── channel.json          # list of [{id, title, upload_date, duration_string, view_count, url}, ...]
└── {video-slug}/         # one subdirectory per pulled video (same structure as single video)
    ├── transcript.txt
    ├── transcript.json
    ├── metadata.json
    └── analysis.md
```

## Execution Pipeline

### Step 1 — Parse Input

Determine the mode based on the input:

**Single video mode** — extract the video ID from the URL:
```
youtube.com/watch?v=XXXXXXXXXXX → XXXXXXXXXXX
youtu.be/XXXXXXXXXXX            → XXXXXXXXXXX
youtube.com/embed/XXXXXXXXXXX   → XXXXXXXXXXX
Raw 11-char string              → use directly
```
Validate: 11 characters, alphanumeric plus hyphens and underscores.

**Channel mode** — if the input is a channel name, @handle, or channel URL, use channel mode. Detect by:
- Starts with `@` → channel handle
- Contains `youtube.com/@` or `youtube.com/c/` → channel URL
- User explicitly says "channel" or uses `--channel` → channel mode

### Step 1b — Channel Mode: List Videos

If in channel mode, list recent videos first:

```bash
uv run ~/.claude/skills/yt-pull/fetch_transcript.py \
  --channel CHANNEL_INPUT \
  --max-videos MAX \
  --output-dir OUTPUT_DIR
```

This writes `channel.json` with an array of `{id, title, upload_date, duration_string, view_count, url}`.

**After listing**, present the video list to the user as a numbered table (title, date, duration, views). Then either:
- Pull transcripts for **all listed videos** if the user asked for a batch
- **Ask the user** which videos to pull if they didn't specify
- Pull a specific count (e.g., "last 5 videos") if specified

For each selected video, run Step 2 with that video's ID, using `{output}/{channel-slug}/{video-slug}/` as the output dir.

### Step 2 — Fetch Transcript + Metadata (single step)

This skill ships a bundled helper script. Run it:

```bash
uv run ~/.claude/skills/yt-pull/fetch_transcript.py VIDEO_ID \
  --lang LANG \
  --output-dir OUTPUT_DIR
```

The script handles everything:
1. **Dependency detection** — auto-installs `youtube-transcript-api` or `yt-dlp` if missing
2. **Transcript fetch** — tries `youtube-transcript-api` first (lightweight, structured data with timestamps), falls back to `yt-dlp` + VTT cleaning
3. **Metadata fetch** — uses `yt-dlp --dump-json` for title, channel, date, duration, views, description
4. **Outputs** — writes `transcript.txt`, `transcript.json`, and `metadata.json` to the output dir
5. **Prints** the clean transcript text to stdout

**If the script fails** (e.g., missing Python, permission issues), fall back to manual steps:

#### Manual fallback — install dependencies

Try in this order (modern Linux distros block system-wide pip):

```bash
# uv resolves the script's inline (PEP 723) dependencies automatically.
# If uv itself is missing, install it first:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Manual fallback — fetch transcript

**Primary (youtube-transcript-api):**
```python
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
transcript = api.fetch("VIDEO_ID", languages=["en"])
for entry in transcript:
    print(f"{entry.start:.1f}\t{entry.text}")
```

**Secondary (yt-dlp):**
```bash
yt-dlp --write-auto-sub --sub-lang en --skip-download --sub-format vtt -o "/tmp/yt-pull/%(id)s" "URL"
# If auto-subs fail, try --write-sub instead of --write-auto-sub
```

Then clean the VTT: remove WEBVTT header, timestamp lines, position tags, HTML tags; deduplicate consecutive identical lines; collapse whitespace into paragraphs.

**Note:** yt-dlp may warn about missing JS runtime (deno). It usually still works, but if it fails, install deno: `curl -fsSL https://deno.land/install.sh | sh`

#### Manual fallback — fetch metadata

```bash
yt-dlp --dump-json --skip-download "URL" | python3 -c "
import json, sys; d = json.load(sys.stdin)
print(json.dumps({k: d.get(k,'') for k in ['title','channel','upload_date','duration_string','view_count']}, indent=2))
"
```

### Step 3 — Analyze via Subagent

**Do NOT read the transcript files in the main conversation.** Spawn a subagent to keep the (potentially massive) transcript out of the primary context window.

Use the **Agent tool** with the following prompt structure. Substitute `{output-dir}`, `{depth}`, and `{video-slug}` with actual values:

```
You are analyzing a YouTube video transcript.

## Files to read

- `{output-dir}/transcript.txt` — clean plain text transcript
- `{output-dir}/transcript.json` — structured entries with timestamps [{text, start, duration}, ...]
- `{output-dir}/metadata.json` — video title, channel, date, duration, views, description

Read all three files, then produce a structured analysis at **{depth}** depth.

## Depth: brief

- 2-3 sentence summary
- 3-5 key takeaways as bullet points

## Depth: standard

- **Video Info**: Title, channel, date, duration
- **Summary**: 1-2 paragraph overview of the content
- **Key Points**: 5-8 bullet points capturing the main ideas
- **Notable Quotes**: 2-3 direct quotes from the transcript that are particularly insightful
- **Topics Covered**: List of main topics/themes discussed

## Depth: deep

- Everything in standard, plus:
- **Detailed Outline**: Timestamped section-by-section breakdown (use transcript.json for timestamps)
- **Argument Analysis**: Assessment of claims made — are they supported? Any logical gaps?
- **Audience & Context**: Who is this video for? What prior knowledge is assumed?
- **Connections**: Related topics, videos, or resources that would complement this content
- **Critical Assessment**: Strengths and weaknesses of the content, potential biases
- **Action Items**: Concrete next steps or takeaways a viewer could act on

## Output

1. Write the full analysis as `{output-dir}/analysis.md`
2. Return a **short summary** (under 200 words) to relay to the user — include the video title, a 2-3 sentence overview, and the path to analysis.md. Do NOT return the full analysis text.

If auto-generated captions were used (check metadata or transcript source), note that transcription errors may be present.
```

For **channel mode** with multiple videos, spawn one subagent per video in parallel.

### Step 4 — Output Results

1. Relay the subagent's short summary to the user
2. Point the user to `{output}/{video-slug}/analysis.md` for the full analysis
3. For very long videos (>2 hours), warn that the transcript was large and analysis may be incomplete

## Error Handling

| Error | Action |
|-------|--------|
| Invalid URL / video ID | Tell the user the URL format isn't recognized, show supported formats |
| Video not found / private | Report that the video is unavailable or private |
| No subtitles available | Report no captions found; suggest the video may not have subtitles enabled |
| Language not available | List available languages and ask user to pick one |
| Script fails | Fall back to manual steps above |
| Both methods fail | Report the failure, suggest the user check if the video has captions |
| Network error | Report connectivity issue |

## Notes

- This skill does NOT download any video or audio — only subtitles/captions and metadata
- `youtube-transcript-api` is preferred over `yt-dlp` for transcripts: lighter weight, returns structured timestamps natively, no JS runtime needed
- `yt-dlp` is still needed for metadata (title, channel, views) and as a transcript fallback
