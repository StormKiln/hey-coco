import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_rendered_plist_is_valid():
    out = subprocess.run(
        ["make", "-s", "-C", str(REPO), "hub-render-plist", "UV_BIN=/opt/uv/bin/uv"],
        check=True,
        capture_output=True,
    ).stdout
    assert b"@" not in out  # every placeholder substituted
    plist = plistlib.loads(out)
    assert plist["Label"] == "com.stormkiln.coco-hub"
    assert plist["ProgramArguments"][:2] == ["/opt/uv/bin/uv", "run"]
    assert plist["ProgramArguments"][-2:] == ["-m", "coco_hub"]
    assert plist["WorkingDirectory"] == str(REPO / "hub")
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert "/Library/Logs/CocoHub/" in plist["StandardOutPath"]
    assert "/Library/Logs/CocoHub/" in plist["StandardErrorPath"]
