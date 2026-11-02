import glob
import json
import os

import pexpect
import pytest
from pexpect.spawnbase import SpawnBase

from pdudaemon.drivers.driver import PDUDriver

RECORDINGS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "recordings", "*", "*.json")))


class Replay(SpawnBase):
    def __init__(self, events):
        super().__init__(timeout=1)
        self.events = [(event["dir"], event["data"].encode("latin-1")) for event in events]
        self.recorded_sends = b"".join(data for direction, data in self.events if direction == "send")
        self.index = 0
        self.sent = b""
        self.released = b""
        self.pending = b""
        self.closed = False
        self._release()

    def _release(self):
        while self.index < len(self.events):
            direction, data = self.events[self.index]
            if direction == "send":
                if len(self.sent) < len(self.released) + len(data):
                    break
                self.released += data
            else:
                self.pending += data
            self.index += 1

    def send(self, s):
        data = self._coerce_send_string(s)
        offset = len(self.sent)
        self.sent += data
        if not self.recorded_sends.startswith(self.sent):
            raise AssertionError("Driver sent %r after %r, recording expected %r" % (
                data, self.sent[max(0, offset - 40):offset], self.recorded_sends[offset:offset + len(data) + 20]))
        self._release()
        return len(data)

    def read_nonblocking(self, size=1, timeout=None):
        if self.pending:
            data = self.pending[:size]
            self.pending = self.pending[size:]
            return data
        if self.index >= len(self.events):
            self.flag_eof = True
            raise pexpect.EOF("End of recording")
        raise pexpect.TIMEOUT("Driver is waiting for output, recording expects %r to be sent first" % (
            self.recorded_sends[len(self.sent):len(self.sent) + 40]))

    def isalive(self):
        return not self.closed and self.index < len(self.events)

    def close(self, force=True):
        self.closed = True


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda path: os.path.relpath(path, os.path.dirname(__file__)))
def test_recording(mocker, path):
    with open(path) as f:
        recording = json.load(f)
    meta = recording["meta"]
    replay = Replay(recording["events"])
    mocker.patch("pexpect.spawn", return_value=replay)
    driver = PDUDriver.select(meta["driver"])("pdu", meta["settings"])

    driver.handle(meta["request"], meta["port"])

    assert replay.sent == replay.recorded_sends
