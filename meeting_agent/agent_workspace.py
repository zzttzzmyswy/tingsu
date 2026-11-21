"""Persistent working directories for meeting agents and upgrades of previously shared files."""

import hashlib
from pathlib import Path

from .agent_materials import MaterialLibrary


def _key(scope):
    return hashlib.sha256(scope.encode()).hexdigest()


class MeetingWorkspace:
    def __init__(self, data, meeting, topic=None):
        self.data = Path(data)
        self.meeting = meeting
        self.topic = topic
        scope = "topic:" + topic if topic else "meeting:" + meeting
        self.directory = (self.data / "workspaces" / _key(scope)[:24]).resolve()

    def library(self, meeting):
        key = _key("meeting:" + meeting)
        return MaterialLibrary(self.directory / "attachments" / key[:16],
                               text_cache=self.data / "material-text" / key)

    def prepare(self, meetings):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for mid in meetings:
            library = self.library(mid)
            library.import_previous(self.data / "materials" / _key("meeting:" + mid), legacy=True)
            if self.topic:
                old = MeetingWorkspace(self.data, mid)
                library.import_previous(old.library(mid).directory)
