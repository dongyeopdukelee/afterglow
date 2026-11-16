"""Obtain Satoshi from Fontshare for this project's own self-hosted website.

The font binary is ignored by Git because the ITF license limits redistribution.
Only the official, unmodified webfont is used; no installer is executed.
"""

import hashlib
import io
import urllib.request
from pathlib import Path
from zipfile import ZipFile

FONT_SHA256 = (
    "e739aff9b4d02c264341d6d4872edcda28e79373aeda936f659566a1cd3eb47f"
)
DESTINATION = Path(__file__).resolve().parents[1] / "frontend/public/fonts"


def main() -> None:
    path = DESTINATION / "Satoshi-Variable.woff2"
    if (
        path.exists()
        and hashlib.sha256(path.read_bytes()).hexdigest() == FONT_SHA256
    ):
        print("Satoshi webfont ready (local verified copy).")
        return
    request = urllib.request.Request(
        "https://api.fontshare.com/v2/fonts/download/satoshi",
        headers={"User-Agent": "AfterglowFontSetup/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        archive = response.read(10_000_001)
    if len(archive) > 10_000_000:
        raise ValueError("Fontshare archive exceeds the setup size limit")
    with ZipFile(io.BytesIO(archive)) as fonts:
        entry = fonts.getinfo(
            "Satoshi_Complete/Fonts/WEB/fonts/Satoshi-Variable.woff2"
        )
        if entry.file_size > 150_000:
            raise ValueError("Unexpected Satoshi font size")
        body = fonts.read(entry)
        license_entry = fonts.getinfo("Satoshi_Complete/License/FFL.txt")
        if license_entry.file_size > 50_000:
            raise ValueError("Unexpected Fontshare license size")
        license_text = fonts.read(license_entry)
    if hashlib.sha256(body).hexdigest() != FONT_SHA256:
        raise ValueError(
            "Satoshi changed upstream; review the font and license before "
            "updating its pinned checksum"
        )
    DESTINATION.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    (DESTINATION / "Satoshi-FFL.txt").write_bytes(license_text)
    print("Satoshi webfont downloaded and verified from Fontshare.")


if __name__ == "__main__":
    main()
