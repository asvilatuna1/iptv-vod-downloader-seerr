import sys

from iptv_vod_downloader.gui import run_app


if __name__ == "__main__":
    try:
        run_app()
    except KeyboardInterrupt:
        sys.exit(130)
