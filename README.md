# kiko

`kiko` is a Python CLI for importing and exporting Google Keep notes to a markdown
directory.

It uses the official Google Keep API, manages dependencies with `uv`, and stores
sync metadata in an HTML comment footer at the bottom of each exported markdown file.

## Commands

```bash
uv run kiko auth setup
uv run kiko auth login
uv run kiko auth status
uv run kiko auth logout
uv run kiko export /path/to/notes-dir
uv run kiko import /path/to/notes-dir
```

`auth setup` does one of two things:

- If it can already find a client JSON, it stores that file at
  `~/.config/kiko/credentials.json`.
- If it cannot find one, it prints the supported Google Cloud console steps for
  enabling the Keep API on the project and then creating a Desktop app OAuth
  client or downloading JSON from an existing one.

`auth setup` resolves OAuth client credentials in this order:

1. `--credentials /path/to/credentials.json`
2. The previously saved credentials path in `~/.config/kiko/config.json`
3. `~/.config/kiko/credentials.json`
4. `./credentials.json`

`auth login` then uses the stored credentials file and runs the installed-app OAuth
browser flow.

## Notes

- Export supports downloading note attachments.
- Import supports note text and checklist content only. Local attachments are detected and
  reported, but the official Keep API does not support uploading them.
- Re-importing an existing tracked note uses the footer metadata to detect stale local files.
