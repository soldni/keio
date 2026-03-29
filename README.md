# kiko

`kiko` is a Python CLI for importing and exporting Google Keep notes to a markdown
directory.

It uses the official Google Keep API, manages dependencies with `uv`, and stores
sync metadata in an HTML comment footer at the bottom of each exported markdown file.

## Commands

```bash
uv run kiko auth login --credentials /path/to/credentials.json
uv run kiko auth status
uv run kiko auth logout
uv run kiko export /path/to/notes-dir
uv run kiko import /path/to/notes-dir
```

## Notes

- Export supports downloading note attachments.
- Import supports note text and checklist content only. Local attachments are detected and
  reported, but the official Keep API does not support uploading them.
- Re-importing an existing tracked note uses the footer metadata to detect stale local files.
