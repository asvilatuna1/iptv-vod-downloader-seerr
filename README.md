# IPTV VOD Downloader

Desktop application written in Python for browsing and downloading VOD content from IPTV providers compatible with the Xtream Codes API.

## License

This project is released under the MIT license. See `LICENSE`.

## Intended Use

This software is intended only for accessing and downloading content you are authorized to use.
You are responsible for complying with the laws, service terms, and copyright rules that apply in your jurisdiction.

## Features

- Save IPTV connection settings locally.
- Test server credentials before loading the catalog.
- Browse movie and TV series categories.
- Search results by title.
- Sort catalog results by title or year.
- Queue movies directly from the main catalog.
- Open a dedicated episode browser for TV series.
- Queue a full season, selected episodes, or an entire series.
- Maintain a download queue with:
  - graphical progress bars with percentage labels
  - retry for failed downloads
  - queue filtering and sorting
  - persisted queue state across app restarts
- Highlight items that are already downloaded or already queued.
- Organize downloads automatically into portable English folder names:
  - `Movies/`
  - `Series/<Series Name (Year)>/Season XX/`

## Requirements

- Python 3.9 or newer
- Windows is the primary target for the packaged build

Install dependencies with:

```bash
pip install -r requirements.txt
```

Run the test suite with:

```bash
python -m unittest -v
```

## Run From Source

```bash
python main.py
```

Application data is stored under:

```text
~/.iptv_vod_downloader/
```

That folder contains:

- `config.json`: saved IPTV connection settings
- `queue_state.json`: persisted queue entries
- `ui_state.json`: saved window/UI preferences
- `app.log`: runtime log file

## How It Works

1. Enter the IPTV server URL, username, password, and download folder.
2. Click `Test connection` to validate credentials.
3. Click `Refresh catalog` to load movies and series.
4. Queue movies directly from the catalog, or open a series to select episodes.
5. Use the download queue to start, pause, stop, retry, filter, or remove items.

## Download Behavior

- Downloads are processed sequentially.
- Existing completed files are treated as already downloaded.
- Partial `.part` files are reused when the server supports ranged downloads.
- Duplicate queue entries are skipped based on target path and item identity.

## Local Build

To generate a local portable Windows build:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onedir --windowed --name iptv-vod-downloader main.py
```

This creates a portable folder in:

```text
dist/iptv-vod-downloader/
```

Run:

```text
dist/iptv-vod-downloader/iptv-vod-downloader.exe
```

## GitHub Actions Release Pipeline

The repository includes a GitHub Actions workflow in:

```text
.github/workflows/build-release.yml
```

Behavior:

- Push to `main`: builds a Windows portable artifact
- Manual workflow run: builds a Windows portable artifact
- Push a tag matching `v*`: builds a portable ZIP and publishes it to GitHub Releases

Example:

```bash
git tag v1.0.0
git push origin v1.0.0
```

That creates a release asset named:

```text
iptv-vod-downloader-windows-x64.zip
```

Extract the ZIP and launch:

```text
iptv-vod-downloader.exe
```

## Notes

- The app targets Xtream Codes compatible endpoints exposed through `player_api.php`.
- Posters are downloaded when available for series episode browsing.
- The UI is implemented with Tkinter.
- The project currently uses a single download worker by design.
