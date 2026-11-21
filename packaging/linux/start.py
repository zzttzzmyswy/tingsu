"""Private launcher used inside the Linux AppImage."""

import sys
from meeting_agent.__main__ import main

sys.exit(main())
