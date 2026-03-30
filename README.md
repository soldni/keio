<p align="center">
    <img src="https://github.com/soldni/keio/blob/main/assets/keio-logo.png?raw=true" alt="keio logo" width="256" />
</p>

# KeIO

A Python CLI for importing and exporting Google Keep notes to markdown directories.

KeIO stores sync metadata in an HTML comment footer at the bottom of each exported
markdown file, enabling conflict detection and incremental syncing.

Fun fact: KeIO stands for "Keep I/O" 😊

## Installation

```bash
pip install keio
```

Or with `uv`:

```bash
uv tool install keio
```

To use the `gkeepapi` backend (see [Authentication](#authentication) below):

```bash
pip install "keio[gkeepapi]"
```

## Authentication

KeIO supports two authentication backends. You must pick one; they cannot be mixed.

### Option A: Enterprise (official Google Keep API)

> [!WARNING]
> The official Google Keep API is **only available to Google Workspace
> Enterprise users**. It will not work with free `@gmail.com` accounts or non-Enterprise
> Workspace plans.

This backend uses the official REST API with a standard OAuth 2.0 Desktop App flow.

1. Go to [Google Cloud Console](https://cloud.google.com/).
2. Create or select a Google Cloud project.
3. Search for "Google Keep API", open it, and click **Enable**.
4. Search for "Google Auth platform" and open it.
5. If no app exists yet, use the setup wizard to create one.
6. Open **Clients** and create a Desktop app OAuth client (or download JSON from an existing one).
7. Run setup and login:

```bash
keio auth setup --method enterprise --credentials /path/to/credentials.json
keio auth login
```

References:
- [Creating credentials](https://developers.google.com/workspace/guides/create-credentials)
- [Enabling APIs](https://developers.google.com/workspace/guides/enable-apis)

### Option B: `gkeepapi` (unofficial, any Google account)

> [!WARNING]
> This backend uses the unofficial [gkeepapi](https://github.com/kiwiz/gkeepapi)
> library, which reverse-engineers the mobile Google Keep protocol. It can break without
> notice if Google changes their internal API. Attachment download support is limited.

This backend works with **any Google account**, including free `@gmail.com`. Setup requires
obtaining a master token via a Docker one-liner.

**Step 1 -- Get an OAuth token cookie:**

1. Open <https://accounts.google.com/EmbeddedSetup> in your browser.
2. Log in with your Google account and click "I agree".
   (The page may show a loading screen indefinitely after this; that is expected.)
3. Open browser developer tools -> **Application** -> **Cookies**.
4. Copy the value of the `oauth_token` cookie.

**Step 2 -- Get a master token (via Docker):**

```bash
docker run --rm -it python:3 sh -c '
  pip install -q gpsoauth && python3 -c "
import gpsoauth,secrets,json
e=input(\"Email: \")
t=input(\"OAuth token: \")
a=secrets.token_hex(8)
r=gpsoauth.exchange_token(e,t,a)
print(json.dumps({\"email\":e,\"master_token\":r[\"Token\"]}))"'
```

**Step 3 -- Pass the JSON to keio:**

You can pass the JSON output directly:

```bash
keio auth setup --method gkeepapi --credentials '{"email":"...","master_token":"..."}'
```

Or save it to a file first:

```bash
keio auth setup --method gkeepapi --credentials /path/to/token.json
```

Then verify:

```bash
keio auth login
```

References:
- [gpsoauth alternative flow](https://github.com/simon-weber/gpsoauth#alternative-flow)
- [gkeepapi docs](https://gkeepapi.readthedocs.io/en/latest/)

### Other auth commands

```bash
keio auth status     # Show current method and login state
keio auth logout     # Remove cached tokens
```

## Usage

### Export

Export your Google Keep notes to a local directory:

```bash
keio export /path/to/notes
```

- Downloads note text, checklists, and attachments.
- Skips locally modified files unless `--force` is used.
- Use `--dry-run` to preview without writing.

### Import

Import markdown files back to Google Keep:

```bash
keio import /path/to/notes
```

- Supports note text and checklist content.
- Uses footer metadata to detect stale local files and avoid conflicts.
- Skips notes whose remote version is newer unless `--force` is used.
- Use `--dry-run` to preview without creating or replacing notes.

### Image upload (`--images`)

> [!IMPORTANT]
> Neither the official Keep API nor gkeepapi supports uploading attachments
> programmatically. Export can download images, but import cannot upload them.

The `--images` flag provides a semi-automated workaround:

```bash
keio import /path/to/notes --images
```

For each note that has local attachments, `--images` will:

1. Create or update the note text in Google Keep as usual.
2. Open the note in your browser and the attachment folder in your file explorer.
3. Poll the note for up to 5 minutes, waiting for you to drag-and-drop the images into Keep manually.
4. Press **Enter** at any time to skip a note and move on.

This makes it practical to re-attach images in bulk without hunting for each note by hand.

### Common flags

| Flag | Applies to | Description |
|---|---|---|
| `--dry-run` | export, import | Preview changes without writing |
| `--force` | export, import | Ignore conflict checks and overwrite |
| `--credentials PATH` | export, import | Override OAuth credentials file |
| `--images` | import | Assist with manual image upload (see above) |

## Development

```bash
git clone https://github.com/soldni/keio.git
cd keio
uv sync --extra dev
uv run pytest
```

## License

Apache-2.0
