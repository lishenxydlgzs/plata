# YouTube playlist audio sync

Persist parent-configured playlist jobs in `data/playlist-sync.sqlite3`. Each job
accepts a YouTube playlist URL, a descriptive folder name, and an interval in hours
(default 24, minimum 1). A single background worker starts with FastAPI and polls
once per minute; jobs survive restarts and missed runs execute once on startup.
Only one sync runs at a time. This assumes the existing single server process.

Expose list/create, update cadence/enabled, and queue-now endpoints under
`/media/sync-jobs`, available through the existing FastAPI `/docs` UI. These are
trusted household network administration endpoints, like the existing APIs.

Use yt-dlp to enumerate public/unlisted playlists, then download each missing
video's best audio and transcode to MP3 with FFmpeg. Validate and canonicalize
YouTube URLs. Never execute a shell or accept arbitrary download URLs. Folder
names are normalized and suffixed with the playlist ID to avoid collisions.
Files have descriptive titles and video IDs for deduplication. Downloads occur in
an ignored staging directory on the media filesystem and are atomically moved
into the playlist folder only after successful conversion. Existing tracks are
kept, including videos removed from the source. Source reordering is not mirrored;
playback uses the existing filename sort. Failed items retry on the next run;
one unavailable video does not prevent other videos being imported.

Record last attempt, next run, state, imported count and bounded error details.
Cancel subprocess groups on server shutdown, including FFmpeg children. Refresh
the media cache after publishing each track so playback sees additions immediately.
Do not fetch private playlists or use account cookies in this first version.

Runtime prerequisites: `yt-dlp[default]`, FFmpeg/ffprobe, and a supported JavaScript
runtime (Deno recommended by yt-dlp). See https://github.com/yt-dlp/yt-dlp.
Tests mock extraction and conversion; no live YouTube requests or Gemini calls.
