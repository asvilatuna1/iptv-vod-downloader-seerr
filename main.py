import logging
import sys

from iptv_vod_downloader.config import CONFIG_DIR
from iptv_vod_downloader.gui import run_app


def configure_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=CONFIG_DIR / "app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":
    configure_logging()
    try:
        run_app()
    except KeyboardInterrupt:
        sys.exit(130)
