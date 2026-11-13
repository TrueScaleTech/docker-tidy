"""Regenerate the README screenshots and sample output from the invented host.

    python tools/screenshot.py

The data comes from tests/scenario.py: every name, id and size in it is made up. Nothing
here talks to a real Docker daemon, so what the screenshots show is only ever that data.
"""

from __future__ import annotations

import asyncio
import io
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from textual.widgets import TabbedContent  # noqa: E402

from docker_tidy.cli import main  # noqa: E402
from docker_tidy.tui.app import DockerTidyApp  # noqa: E402
from fakes import FakeDocker  # noqa: E402

NOW = datetime(2026, 11, 13, tzinfo=UTC)
IMAGES = ROOT / "docs" / "images"


async def screenshots() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    app = DockerTidyApp(FakeDocker(), now=lambda: NOW)
    async with app.run_test(size=(132, 26)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#tabs", TabbedContent).active = "tab-image"
        await pilot.pause()
        # Tick a third image by hand, so the screenshot shows a mixed selection.
        await pilot.press("down", "down", "space")
        await pilot.pause()
        (IMAGES / "docker-tidy.svg").write_text(
            app.export_screenshot(title="docker-tidy"), encoding="utf-8", newline="\n"
        )

        await pilot.press("c")
        await pilot.pause()
        (IMAGES / "docker-tidy-confirm.svg").write_text(
            app.export_screenshot(title="docker-tidy"), encoding="utf-8", newline="\n"
        )


def sample(*argv: str) -> None:
    out = io.StringIO()
    main(list(argv), runner=FakeDocker(), out=out)
    print(f"$ docker-tidy {' '.join(argv)}")
    print(out.getvalue())


if __name__ == "__main__":
    asyncio.run(screenshots())
    if "--samples" in sys.argv:
        sample("report")
        sample("clean")
        sample("clean", "--kind", "volume")
