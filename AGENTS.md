# KiKo Project Guide for AI Agents

## What is KiKo

KiKo is a CLI tool that bidirectionally syncs Google Keep notes with a local directory of markdown files. It supports two Google Keep backends: the official Enterprise REST API and the unofficial `gkeepapi` library.

## Quick Reference

```bash
uv run pytest                  # Run tests (48 tests, ~0.4s)
uv run ruff check src/ tests/  # Lint
uv run ruff format src/ tests/ # Format
kiko export /path/to/notes     # Export Keep notes to markdown
kiko import /path/to/notes     # Import markdown back to Keep
kiko auth setup --method enterprise --credentials /path/to/creds.json
kiko auth login
kiko auth status
```

## Architecture Overview

### Dual Backend Design

KiKo supports two backends behind a shared `KeepClientProtocol` (runtime-checkable Protocol):

| | Enterprise (`KeepClient`) | Unofficial (`GkeepApiClient`) |
|---|---|---|
| **API** | Official REST API via `google-api-python-client` | Unofficial mobile API via `gkeepapi` |
| **Auth** | OAuth2 Desktop App flow | Master token from `gpsoauth` |
| **Requirements** | Google Workspace Enterprise subscription | Any Google account |
| **State** | Stateless (each API call is independent) | Stateful (maintains sync state for incremental sync) |
| **`sync()` method** | No-op | Calls `gkeepapi.Keep.sync()` |
| **Attachment download** | Direct HTTP via `AuthorizedSession` | Via blob URL with `urllib.request.urlretrieve` |
| **Note IDs (`name`)** | Format: `notes/abc123def` | Format: raw note ID string |

The backend is selected automatically based on config (`config.json` stores `method`) or auto-detected from the presence of `master-token.json`.

### Data Flow

```
Export:  Keep API → KeepNote → render_markdown_document() → .md file + attachment dir
Import:  .md file → parse_markdown_file() → ParsedMarkdownNote → Keep API
```

### File Layout on Disk

For a note titled "My Note" with attachments:
```
notes/
  My Note.md            # Markdown file with embedded footer
  My Note/              # Attachment directory (same stem as .md file)
    image.png
    image_2.png
    attachment.pdf
```

### Sync Metadata (Footer)

Every exported markdown file contains an HTML comment footer as its last line:
```
<!-- kiko:{"content_sha256":"abc...","keep_name":"notes/123","keep_update_time":"2026-03-29T12:00:00Z","synced_at":"2026-03-29T12:01:00Z","title_empty":true,"version":1} -->
```

The footer is the single source of truth for tracking sync state. Fields:

- **`version`**: Always `1`. Reserved for future format changes.
- **`keep_name`**: The remote note identifier. Used to look up the remote note on subsequent sync.
- **`keep_update_time`**: The `updateTime` from the API at last sync. Used for conflict detection.
- **`content_sha256`**: SHA-256 hash of the file content WITHOUT the footer line. Used to detect local edits.
- **`synced_at`**: UTC timestamp of when the sync happened. Informational only.
- **`title_empty`**: `true` if the original Keep note had no title. When importing, this prevents the filename from being used as the title.

### Conflict Detection Strategy

**During export:**
1. Build a local index mapping `keep_name` → parsed markdown file
2. For each remote note, check if a local file tracks it (via footer `keep_name`)
3. If tracked: compute `content_sha256` of current file content and compare to footer's stored hash
4. If hashes differ → local file was edited → skip (unless `--force`)
5. If hashes match → safe to overwrite with remote content

**During import:**
1. For each local file with a footer pointing to a remote note:
   - Compare `keep_update_time` in footer vs remote `updateTime` (exact string match)
   - If they differ: the remote was modified since last sync → skip (unless `--force`)
   - If they match: compare local content hash to footer hash
   - If content unchanged → nothing to do
   - If content changed → replace the remote note
2. For local files without a footer (new notes):
   - Check if a remote note with the same title exists → skip to avoid duplicates
   - Otherwise → create a new remote note

### Note Title Resolution

Titles flow through a specific chain:

**Export (Keep → markdown):**
- If note has a title → filename is `sanitized_title.md`, H1 heading in file
- If note has no title → filename is `untitled-{short_id}.md`, no H1 heading, `title_empty: true` in footer
- If multiple notes share a title → disambiguated with `Title [short_id].md`

**Import (markdown → Keep):**
- If file has H1 heading → that's the title (regardless of `title_empty` in footer)
- If file has no H1 AND footer says `title_empty: true` → send empty title to Keep
- If file has no H1 AND no footer or `title_empty: false` → use filename stem as title

This is implemented in `_effective_title()` in `importer.py`.

### Checklist / List Note Detection

On import, the body markdown is tested against `parse_checklist_markdown()`. If every non-blank line matches the pattern `- [ ] text` or `- [x] text` (with up to 2-space indent for children), the note is imported as a list note. If any line has headings, blockquotes, code fences, tables, or regular list items (without checkboxes), it's imported as text.

Google Keep supports exactly ONE level of nesting for list items. The regex enforces `{0,2}` spaces of indent, so only parent (0 spaces) and child (1-2 spaces) levels are supported.

### Attachment Handling

**Export:** Attachments are downloaded to a temp directory, then atomically moved to `<stem>/` alongside the markdown file. Inline markdown references are prepended to the body (`![](stem/image.png)` for images, `[filename](stem/file.ext)` for others).

**Import:** KiKo cannot programmatically upload attachments to Google Keep. When `--images` is passed, it opens the note in the browser and the attachment directory in the file explorer, then polls the API until the expected number of attachments appears (or the user presses Enter to skip, or 5 minutes timeout).

### Authentication Storage

All auth files live in the platform config directory (`platformdirs`, typically `~/.config/kiko/` on Linux, `~/Library/Application Support/kiko/` on macOS):

| File | Purpose |
|---|---|
| `config.json` | Stores `method` and `credentials_path` |
| `credentials.json` | Copied OAuth client credentials (enterprise) |
| `oauth-token.json` | Cached OAuth token (enterprise) |
| `master-token.json` | Email + master token (gkeepapi) |
| `gkeepapi-state.json` | Serialized gkeepapi state for incremental sync |

## Module-by-Module Guide

### `cli.py`
Typer CLI app with two command groups: root (`export`, `import`, `version`) and `auth` (`login`, `logout`, `setup`, `status`). All operations funnel through `_run_operation()` which catches exceptions and converts `OperationSummary` to exit codes. Note: `_run_operation` always raises `typer.Exit`, even on success.

### `auth.py`
Handles credential storage, OAuth flows, and client construction. Key function: `build_keep_client()` is the factory that picks the right backend. Auth method is resolved by: explicit config > auto-detect from `master-token.json` > default to enterprise.

### `client_protocol.py`
Defines `KeepClientProtocol` — the interface both backends implement. `runtime_checkable` so it can be used with `isinstance()`. Also defines `KeepClientError`.

### `keep_client.py`
Enterprise backend. Uses `googleapiclient.discovery.build("keep", "v1")`. The `sync()` method is a no-op. Attachment download uses `AuthorizedSession` with the Keep API's media download endpoint.

### `gkeepapi_client.py`
Unofficial backend wrapping `gkeepapi.Keep`. Caches blob references in `_blob_cache` during `_convert_blobs()` so `download_attachment()` can find them later. Uses `urllib.request.urlretrieve` for downloads. `isinstance` check for list detection imports `gkeepapi.node` at call time.

### `markdown_model.py`
Pure data classes. No logic except `FooterMetadata.to_dict()` which conditionally includes fields (omits None/False values to keep footers compact).

### `markdown_io.py`
Parsing and rendering. Key functions:
- `extract_footer()`: Strips the last line if it matches `<!-- kiko:{...} -->` and parses the JSON
- `parse_markdown_file()`: Full parser — extracts title from H1, strips leading attachment references, separates body from footer
- `parse_checklist_markdown()`: Returns `list[ChecklistItem]` if the body is a valid checklist, `None` if it contains non-checklist elements, or `[]` if empty
- `content_sha256()`: Hashes content after normalizing newlines and stripping trailing newlines

### `exporter.py`
Main export logic. Builds a local index of tracked files, fetches all remote notes, then for each note: checks for conflicts, downloads attachments to a temp dir, renders the markdown document, and atomically writes the file (temp file → rename). Handles title collisions by appending `[short_id]`.

### `importer.py`
Main import logic. Parses all local `.md` files, checks for duplicate titles and duplicate `keep_name` footers, fetches remote notes, then for each local file: decides whether to create, replace, or skip. After create/replace, rewrites the footer in the local file. The `--images` flag enables interactive upload assistance.

### `attachments.py`
MIME type preference ordering and filename generation. Images prefer PNG > JPEG > HEIC > TIFF > WebP > GIF. Filenames: `image.png`, `image_2.png`, `attachment.pdf`, `attachment_2.pdf`.

### `conflicts.py`
Timestamp parsing and comparison helpers. `parse_google_timestamp` handles both `Z` and `+00:00` suffixes. `remote_matches_footer` compares timestamps as exact strings (not parsed datetimes).

### `results.py`
`OperationSummary` aggregates counters and issues. Exit codes: 0 = clean success, 1 = fatal error, 2 = completed with warnings/skips.

## Known Issues & Gotchas

### Bug: Leading-dot titles create hidden/invisible files
`_sanitize_stem()` does not strip leading dots. A note titled `.hidden` becomes `.hidden.md`. On Python 3.12, `Path.glob("*.md")` skips dotfiles, making the file invisible to subsequent export/import operations. On Python 3.13+, glob matches dotfiles, so the file is found — but it's still hidden in file managers (macOS Finder, Linux file managers), which may surprise users.

### Bug: `extract_footer` crashes on malformed JSON
If a file's last line matches `<!-- kiko:{...} -->` but contains invalid JSON, `json.loads()` raises an unhandled `JSONDecodeError`. This can happen with hand-edited files.

### Bug: `logout` + `status` inconsistency for gkeepapi
`logout()` removes `gkeepapi-state.json` but not `master-token.json`. Since `status()` checks `master_token_file.exists()`, `kiko auth status` shows `logged_in: yes` after logout.

### Gotcha: Timestamp comparison is string-based
`remote_matches_footer()` compares timestamps as exact strings. `"2026-03-29T12:00:00.000Z"` != `"2026-03-29T12:00:00Z"` even though they represent the same instant. This can cause unnecessary skips or false conflict detection.

### Gotcha: replace_*_note is not atomic
Both backends implement replace as create-then-delete. If create succeeds but delete fails, you get a duplicate note. This is by design (avoids data loss from delete-first) but can cause duplicates in error scenarios.

### Gotcha: Error detection via string matching
`KeepClient.get_note()` and `delete_note()` check `"404" in str(error)` to detect not-found errors. A note name containing "404" could trigger a false match.

### Gotcha: `--images` with skipped notes
When a note is skipped during import (remote is newer), `_assist_image_upload` is still called if `--images` is set. This opens browser + file explorer for a note whose text wasn't updated.

### Gotcha: Single-level checklist nesting only
The checklist regex allows 0-2 spaces indent, supporting exactly one level of nesting (matching Google Keep's limit). Deeper-indented items cause the entire body to be treated as plain text.

### Gotcha: `_run_operation` always raises `typer.Exit`
Even on exit code 0. This is correct CLI behavior but means the function never returns normally.

### Design: Loose type annotations in auth.py
Many functions return `object` instead of their actual return types (`Credentials`, `gkeepapi.Keep`, `KeepClient`, `GkeepApiClient`). This is intentional to avoid importing gkeepapi at module level (it's an optional dependency), but it prevents static type checking.

### Design: `_utc_now()` is duplicated
Identical implementations exist in both `exporter.py` and `importer.py`.

## Testing

Tests use a `FakeKeepClient` (in `conftest.py`) that stores notes in a dict and generates sequential names/timestamps. The `test_gkeepapi_client.py` tests use separate fakes (`FakeKeep`, `FakeNote`, `FakeList`) that mimic `gkeepapi`'s interface, with a monkeypatch fixture that makes `isinstance` checks work without importing the real `gkeepapi.node` module.

All tests are deterministic and use `tmp_path` for filesystem isolation.
