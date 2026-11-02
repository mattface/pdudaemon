#!/usr/bin/python3

import argparse
import json
import os
import re
import socket
import sys
import time

import pexpect

from pdudaemon.drivers.driver import PDUDriver


class Recorder:
    def __init__(self):
        self.start = time.monotonic()
        self.events = []

    def stream(self, direction):
        recorder = self

        class Stream:
            def write(self, data):
                recorder.events.append({"t": round(time.monotonic() - recorder.start, 3),
                                        "dir": direction, "data": data.decode("latin-1")})

            def flush(self):
                pass

        return Stream()

    def spawn(self, *args, **kwargs):
        child = real_spawn(*args, **kwargs)
        child.logfile_read = self.stream("recv")
        child.logfile_send = self.stream("send")
        return child

    def merged(self):
        events = []
        for event in self.events:
            if events and events[-1]["dir"] == event["dir"]:
                events[-1]["data"] += event["data"]
            else:
                events.append(dict(event))
        return events


def replace_after_prompt(events, prompt, secret, placeholder):
    prompted = False
    for index, event in enumerate(events):
        if event["dir"] == "recv" and prompt in event["data"]:
            prompted = True
        elif event["dir"] == "send" and prompted and secret in event["data"]:
            event["data"] = event["data"].replace(secret, placeholder, 1)
            for echo in events[index + 1:]:
                if echo["dir"] == "recv":
                    if secret in echo["data"]:
                        echo["data"] = echo["data"].replace(secret, placeholder, 1)
                    else:
                        echo["data"] = echo["data"].replace("*" * len(secret), "*" * len(placeholder), 1)
                    break
            return True
    return False


def anonymise(events, addresses, username, password):
    for event in events:
        for address in addresses:
            event["data"] = event["data"].replace(address, "pdu01")
    if password and not replace_after_prompt(events, "assword", password, "password"):
        return False
    if username and not replace_after_prompt(events, "ser", username, "username"):
        return False
    return True


def detect_firmware(events):
    text = "".join(event["data"] for event in events if event["dir"] == "recv")
    firmware = {}
    for key, pattern in (("aos", r"AOS\s+(v[\d.]+)"), ("app", r"APP\s+(v[\d.]+)")):
        match = re.search(pattern, text)
        if match:
            firmware[key] = match.group(1)
    return firmware


def main():
    parser = argparse.ArgumentParser(
        description="Record a session between a pexpect based driver and a real PDU, "
                    "for the replay tests in tests/test_recordings.py. "
                    "This really switches the port, so use one with nothing connected.")
    parser.add_argument("--driver", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--request", required=True, choices=["on", "off"])
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--setting", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--output")
    args = parser.parse_args()

    settings = dict(setting.split("=", 1) for setting in args.setting)
    if args.username:
        settings["username"] = args.username
    if args.password:
        settings["password"] = args.password
    port = int(args.port) if args.port.isdigit() else args.port

    recorder = Recorder()
    pexpect.spawn = recorder.spawn
    driver = PDUDriver.select(args.driver)(args.hostname, settings)
    try:
        driver.handle(args.request, port)
    except Exception:
        driver._bombout()
        raise
    events = recorder.merged()

    addresses = {args.hostname}
    try:
        addresses.add(socket.gethostbyname(args.hostname))
    except OSError:
        pass
    username = getattr(driver, "username", None)
    password = getattr(driver, "password", None)
    if not anonymise(events, addresses, username, password):
        sys.exit("Could not find the username or password in the session, not saving the recording")
    if username:
        settings["username"] = "username"
    if password:
        settings["password"] = "password"

    firmware = detect_firmware(events)
    output = args.output
    if not output:
        if "aos" not in firmware:
            sys.exit("Could not detect the firmware version, use --output to name the recording")
        output = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.driver,
                              "aos%s-%s.json" % (firmware["aos"].lstrip("v"), args.request))
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)

    recording = {
        "meta": {"driver": args.driver, "port": port, "request": args.request,
                 "settings": settings, "firmware": firmware},
        "events": events,
    }
    with open(output, "w") as f:
        json.dump(recording, f, indent=1)
        f.write("\n")
    print("Saved %s" % output)


real_spawn = pexpect.spawn

if __name__ == "__main__":
    main()
