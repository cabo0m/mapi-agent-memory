from pathlib import Path
import py_compile

ROOT = Path(r"C:\path\to\mapi")
FILES = [
    ROOT / "server.py",
    ROOT / "app" / "timeline.py",
]

for path in FILES:
    py_compile.compile(str(path), doraise=True)
    print(f"OK: {path.name}")
