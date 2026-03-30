# KeIO Project Guide for AI Agents

**NOTE:** `CLAUDE.md` and `AGENTS.md` are hard-linked (same file). Edits to either one automatically apply to both. Do not delete and recreate either file — that would break the hard link. Always edit in place. If you notice the hard link is broken (different inodes from `ls -li CLAUDE.md AGENTS.md`), fix it by running `rm AGENTS.md && ln CLAUDE.md AGENTS.md`.

## What is KeIO

KeIO is a CLI tool that bidirectionally syncs Google Keep notes with a local directory of markdown files. It supports two Google Keep backends: the official Enterprise REST API and the unofficial `gkeepapi` library.

## Quick Reference

```bash
uv run pytest                  # Run tests (~0.3s)
uv run ruff check src/ tests/  # Lint
uv run ruff format src/ tests/ # Format
keio export /path/to/notes     # Export Keep notes to markdown
keio import /path/to/notes     # Import markdown back to Keep
keio auth setup --method enterprise --credentials /path/to/creds.json
keio auth login
keio auth status
```

## Architecture Overview

### Dual Backend Design

KeIO supports two backends behind a shared `KeepClientProtocol` (runtime-checkable Protocol):

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
<!-- keio:{"content_sha256":"abc...","keep_name":"notes/123","keep_update_time":"2026-03-29T12:00:00Z","synced_at":"2026-03-29T12:01:00Z","title_empty":true,"version":1} -->
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
   - Compare `keep_update_time` in footer vs remote `updateTime` (string match with datetime fallback)
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

**Import:** KeIO cannot programmatically upload attachments to Google Keep. When `--images` is passed, it opens the note in the browser and the attachment directory in the file explorer, then polls the API until the expected number of attachments appears (or the user presses Enter to skip, or 5 minutes timeout).

### Authentication Storage

All auth files live in the platform config directory (`platformdirs`, typically `~/.config/keio/` on Linux, `~/Library/Application Support/keio/` on macOS):

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
- `extract_footer()`: Strips the last line if it matches `<!-- keio:{...} -->` and parses the JSON. Returns `None` footer if JSON is malformed.
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
Timestamp parsing and comparison helpers. `parse_google_timestamp` handles both `Z` and `+00:00` suffixes. `remote_matches_footer` tries exact string comparison first (fast path), then falls back to parsing both timestamps as datetimes to handle formatting differences like fractional seconds.

### `results.py`
`OperationSummary` aggregates counters and issues. Exit codes: 0 = clean success, 1 = fatal error, 2 = completed with warnings/skips.

## Implementation Deep Dive

This section documents non-obvious implementation details, invariants, and decision logic that are not immediately apparent from reading the code. Future agents should consult this when modifying core logic.

### Export Decision Tree (per remote note)

```
For each remote note:
  1. Compute preferred_stem (sanitized title, or "untitled-{short_id}")
  2. If title collision with another remote note → append " [{short_id}]" to stem
  3. Look up note.name in local index (by footer keep_name)
  4. If tracked locally AND not --force:
     a. Compute content_sha256 of current local file (minus footer)
     b. Compare to footer's stored content_sha256
     c. If DIFFERENT → skip (local edit detected)
     d. If SAME → proceed to overwrite
  5. If --dry-run → log "Would export" and continue (NO attachment download)
  6. Download attachments to temp dir inside target directory
  7. Render markdown document with footer containing new hash
  8. Atomic write: temp file → rename to destination
  9. If tracked file was at a different path (title changed):
     → delete old .md file and old attachment directory
```

Key invariants:
- The content hash stored in the footer is computed on the **rendered** content (title + attachments + body), NOT the raw note body alone. This means the hash covers the H1 heading line and inline attachment references.
- `--dry-run` returns early before any network I/O for attachments. It only requires the initial `list_notes()` call and local index scan.
- The local index is built from `*.md` files in the **top-level** directory only (no recursion). Subdirectories are used for attachments, not nested notes.

### Import Decision Tree (per local file)

```
Pre-checks (before per-file loop):
  1. Parse all *.md files in directory
  2. Check for duplicate keep_names in footers → fatal error if found
  3. Count duplicate titles ONLY among notes without keep_name (create candidates)
  4. Fetch all remote notes, build notes_by_title index

For each local file:
  A. Does it have a footer with keep_name AND that remote note exists?
     YES → REPLACE PATH:
       a. Check remote timestamp vs footer timestamp
          - If different AND not --force:
            - If remote is newer → skip "remote is newer"
            - Else → skip "timestamp mismatch"
          - If same → continue
       b. Check if local content hash changed vs footer hash
          - If same AND not --force → "unchanged" (nothing to do)
       c. Replace remote note (create new + delete old)
       d. Rewrite local footer with new keep_name and timestamp

     NO → CREATE PATH:
       a. Is this title duplicated among other create candidates? → skip
       b. Does a remote note with this title already exist? → skip
       c. Create new remote note
       d. Write footer to local file
       e. Add title to notes_by_title (prevents later files creating dupes)
```

Key invariants:
- The duplicate title check applies ONLY to notes going through the create path. Tracked notes (with keep_name) bypass it entirely. This is critical for untitled notes: multiple tracked notes with `title_empty: true` all have effective_title="" but must still be individually replaceable.
- A tracked note whose remote was deleted (get_note returns None) falls through to the create path. It is then subject to the same title-collision checks as new notes.
- After creating a note, its title is added to `notes_by_title` to prevent later files in the same batch from creating duplicates.

### Content Hash Computation

The hash serves as a "has the user edited this file since last sync?" check.

```python
content_sha256(text_without_footer):
  1. normalize_newlines(text)     # \r\n and \r → \n
  2. .rstrip("\n")                # strip trailing newlines
  3. .encode("utf-8")
  4. SHA-256 hex digest
```

Important: the hash is computed on `raw_content_without_footer` — the full file content minus the footer line and its surrounding whitespace. This includes the H1 title line, inline attachment references, and body. It does NOT include the footer itself.

During export, the hash is computed on the **rendered** content from `render_markdown_content()`. During import, the hash is computed on the **parsed** content from `extract_footer()`. These produce the same string because:
- `render_markdown_content` joins segments with `\n\n` and strips trailing `\n`
- `extract_footer` strips the footer line and trailing empty lines, then `.rstrip("\n")`
- Both normalize to the same form

If a user adds trailing whitespace, extra blank lines between sections, or edits the H1, the hash changes and the file is treated as locally modified. This is intentional.

### Footer Lifecycle

1. **Created during export**: Contains keep_name, keep_update_time, content_sha256 of rendered content, synced_at timestamp, and title_empty flag.
2. **Read during import**: Parsed to determine keep_name (for remote lookup), keep_update_time (for conflict detection), content_sha256 (for local-edit detection), and title_empty (for title resolution).
3. **Rewritten after import create/replace**: New footer has the NEW note's keep_name and update_time (since replace creates a new note), fresh content_sha256 and synced_at, and title_empty based on the effective title sent to Keep.
4. **Read during next export**: Local index maps keep_name → file. Hash comparison detects local edits.

The footer uses `<!-- keio:{json} -->` format. For backward compatibility, `<!-- kiko:{json} -->` is also parsed (the project was renamed from KiKo to KeIO). New footers always use `keio:`.

`FooterMetadata.to_dict()` omits falsy fields to keep footers compact. This means:
- `keep_name=None` → field absent
- `title_empty=False` → field absent (only `True` is stored)
- `content_sha256=None` → field absent

### Attachment Reference Parsing

When parsing a markdown file, leading attachment references (between the H1 title and the body) are stripped from the body and stored separately. This prevents round-trip drift when a note is exported (references prepended) and then imported.

The parser in `_consume_leading_attachment_lines()`:
1. Starts after the title (and optional blank line after title)
2. Each line is tested: is it a local file reference? (`![](path)` or `[name](path)` where path has no URL scheme)
3. Consecutive reference lines are collected as `inline_references`
4. A blank line after references ends the block
5. Everything after is the body

"Local reference" means the target path has no URL scheme (`http:`, `https:`, etc.). This distinguishes `![](note/image.png)` (local attachment → strip) from `![](https://example.com/img.png)` (external link → keep in body).

If a file has no leading attachment references, the body starts immediately after the title. The `_consume_leading_attachment_lines` function handles this by returning the same cursor position if the first non-blank line isn't a reference.

### Effective Title Resolution (`_effective_title`)

```python
def _effective_title(note: ParsedMarkdownNote) -> str:
    if note.footer and note.footer.title_empty and not note.title_from_h1:
        return ""
    return note.title
```

This encodes the following precedence:
1. If the file has an H1 heading → that's the title (regardless of footer flags)
2. If footer says `title_empty=True` and no H1 → empty string (preserve untitled status)
3. Otherwise → `note.title` (which defaults to the filename stem if no H1)

Scenario matrix:

| H1 present? | footer.title_empty | Result |
|---|---|---|
| Yes | any | H1 text |
| No | True | `""` (empty — note stays untitled in Keep) |
| No | False/absent | filename stem |
| No | no footer | filename stem |

### gkeepapi Child Item Handling

The `gkeepapi` library's `createList()` only accepts flat `(text, checked)` tuples — no nesting. To create child items:

1. Call `keep.createList(title, flat_items)` with only top-level items
2. Take a snapshot of `node.items` (before adding children)
3. For each source item that has children:
   - `node.add(child.text, child.checked)` adds the child as a new top-level item
   - `child_node.indent(parent)` moves it under the parent
4. Call `keep.sync()` to push changes

The snapshot at step 2 is important: `node.add()` appends to `node.items`, so without the snapshot, the enumeration index would drift as children are added.

### Filename Sanitization

`_sanitize_stem()` in `exporter.py`:
1. Replace characters in `<>:"/\\|?*` and control chars (ord < 32) with `_`
2. Strip leading/trailing whitespace
3. Strip leading/trailing dots (prevents hidden files on Unix, reserved names on Windows)
4. If result is empty → fall back to `"untitled"`

Edge cases:
- Title `"..."` → sanitized to `""` → falls back to `"untitled"`
- Title `".hidden"` → sanitized to `"hidden"` (dots stripped)
- Title with path separators like `"A/B"` → sanitized to `"A_B"`
- The H1 heading inside the file preserves the original title, even if the filename differs

There is no truncation for long titles. Extremely long titles (>255 chars) may fail on some filesystems.

### Replace Operation Semantics

Both backends implement `replace_*_note` as **create-then-delete**:
1. Create a new note with the updated content → get new name/timestamp
2. Delete the old note by name
3. Return the new note

This means:
- The note gets a NEW `keep_name` after replacement. The footer must be rewritten.
- If create succeeds but delete fails, a duplicate note exists (documented gotcha)
- The note type can change (text → list or vice versa) since it's a fresh creation

### Error Propagation

```
KeepClientError (from backends)
  → caught in exporter: skip note, add issue, continue
  → caught in importer: not caught (propagates to _run_operation)

AuthError (from auth.py)
  → caught in CLI: print message, exit 1

OperationSummary.fatal = True
  → exit code 1, stops processing immediately (used for duplicate keep_names)

OperationSummary with warnings/skips
  → exit code 2 (completed with issues)

_run_operation always raises typer.Exit
  → even on success (exit code 0)
```

### `--dry-run` Behavior

**Export dry-run:**
- Fetches remote note list (one API call)
- Builds local index (reads local files)
- Performs conflict checks (hash comparison)
- Does NOT download attachments
- Does NOT write any files
- Reports what would be exported or skipped

**Import dry-run:**
- Scans local markdown files
- Fetches remote note list
- Performs all conflict/duplicate checks
- Does NOT create, replace, or delete notes
- Does NOT rewrite footers
- Reports what would be created, replaced, or skipped

### `--images` Interactive Upload Flow

When `--images` is passed during import:
1. After creating/replacing a note that has local attachment files:
   - Opens the note in the browser (`https://keep.google.com/#NOTE/{id}`)
   - Opens the attachment directory in the system file explorer
   - Polls `client.get_note()` every 5 seconds for up to 5 minutes
   - User manually drags files into the browser
   - Polling stops when `len(note.attachments) >= expected` or user presses Enter
2. This also runs for **skipped** notes if `--images` is set (documented gotcha)
3. On non-TTY stdin, the Enter-to-skip check is disabled

## Known Issues & Gotchas

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

## Release Notes

Release notes live in `release-notes/<version>.md`. When making commits that add features, fix bugs, or introduce meaningful changes, update the release notes file for the current development version.

### Format

Each release notes file follows this structure:

```
# Release Notes (<version>)

## New Features
- Description of new commands or capabilities.

## Changes
- Breaking changes, dependency updates, or behavioral changes.

## Fixes
- Bug fixes.

## Housekeeping
- Code refactors, CI changes, tooling updates, or other non-user-facing work.


**Full Changelog**: https://github.com/soldni/keio/compare/<previous-tag>...<current-tag>
```

Only include sections that have entries. Each bullet should be concise — one or two sentences max. Use backticks for command names, flags, and code references.

### When to update

Update the release notes file as part of the same commit that introduces the change. If no release notes file exists yet for the current version, create one. The current version can be found in `src/keio/version.py`; the matching release notes file will be named `release-notes/<version>.md`.

## Commit Guidelines

### Update release notes

You should update the release notes file in `release-notes/` matching the current version of this software. You can find current version at `src/keio/version.py`; the matching release notes file will be named `<version>.md`. If it doesn't exist, create it.

### Sign-off

All commits made by AI agents (Claude, Codex, etc.) **must** include a sign-off line with the model name and version:

```
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
Co-Authored-By: GPT-4.1 <noreply@openai.com>
Co-Authored-By: Gemini 2.5 Pro <noreply@google.com>
```

Use the actual model name and version that generated the code. This applies to all AI models, not just Claude.
