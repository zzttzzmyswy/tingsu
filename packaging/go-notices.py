"""Collect licensing for the Go runtime and the selected versions in the Go module graph."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)
root = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
runtime_license = next((path for path in (root / "LICENSE", Path("/usr/share/licenses/go/LICENSE")) if path.is_file()), None)
if runtime_license is None:
    raise SystemExit("Go distribution LICENSE is missing")
shutil.copyfile(runtime_license, dest / "go-LICENSE")
graph = subprocess.check_output(["go", "list", "-m", "-json", "all"], text=True)
decoder = json.JSONDecoder()
while graph.strip():
    mod, n = decoder.raw_decode(graph.lstrip())
    graph = graph.lstrip()[n:]
    if mod.get("Main") or not mod.get("Dir"):
        continue
    location = Path(mod["Dir"])
    target = dest / (mod["Path"] + "@" + mod["Version"])
    for notice in sorted(location.iterdir()):
        if notice.is_file() and notice.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS", "PATENTS")):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(notice, target / notice.name)
