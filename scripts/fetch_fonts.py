"""Fetch the two OFL-licensed fonts shipped with StudioFlow (build-time only)."""

from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1] / "resources" / "fonts"
root.mkdir(parents=True, exist_ok=True)
files = {
    "Cinzel.ttf": "cinzel/Cinzel%5Bwght%5D.ttf",
    "Cinzel-OFL.txt": "cinzel/OFL.txt",
    "Roboto.ttf": "roboto/Roboto%5Bwdth,wght%5D.ttf",
    "Roboto-OFL.txt": "roboto/OFL.txt",
}
for name, source in files.items():
    data = urllib.request.urlopen(
        "https://raw.githubusercontent.com/google/fonts/main/ofl/" + source, timeout=30
    ).read()
    (root / name).write_bytes(data)
    print(name, len(data))
