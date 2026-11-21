"""Start the packaged desktop application and its bundled native tools."""

import os
import sys
from pathlib import Path

bundle = Path(sys._MEIPASS)
os.environ["TINGSU_HOST_PATH"] = os.environ["PATH"]
os.environ["PATH"] = os.pathsep.join((str(bundle / "bin"), str(bundle), os.environ["PATH"]))

from meeting_agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
