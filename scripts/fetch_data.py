"""Download the Open University Learning Analytics Dataset into data/raw and check its hash.

Usage: python scripts/fetch_data.py
The data is published under CC BY 4.0. The raw files are not committed (about 450 MB unpacked).
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "data" / "source_manifest.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    archive = raw / "oulad.zip"
    if not archive.exists() or sha256(archive) != MANIFEST["archive"]["sha256"]:
        print("downloading", MANIFEST["archive"]["url"])
        urllib.request.urlretrieve(MANIFEST["archive"]["url"], archive)
    if sha256(archive) != MANIFEST["archive"]["sha256"]:
        sys.exit("the archive does not match the recorded SHA-256; the publisher may have changed the file")
    with zipfile.ZipFile(archive) as z:
        for name, info in MANIFEST["files"].items():
            z.extract(name, raw)
            if sha256(raw / name) != info["sha256"]:
                sys.exit(f"{name} does not match the recorded SHA-256")
            print(f"ok {name} ({info['rows']:,} rows)")


if __name__ == "__main__":
    main()
