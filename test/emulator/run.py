#!/usr/bin/env python3
"""Deterministic, non-AI acceptance checks against the installed watch binary."""

import argparse
import csv
import fnmatch
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import zipfile
from dataclasses import dataclass, replace

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "build/host-tests"
KINDS = {"sun": 0, "moon": 1, "planet": 2, "pluto": 3, "minor": 4}
SIZES = {"basalt": (144, 168), "chalk": (180, 180), "diorite": (144, 168),
         "emery": (200, 228), "flint": (144, 168), "gabbro": (260, 260)}
LOW_MEMORY_PLATFORMS = {"basalt", "chalk", "diorite", "flint"}
FATAL = re.compile(r"(?:ASTRO_ERROR|\bERROR\b|\bE\s+\d\d:\d\d:\d\d|"
                   r"app fault|app crash|assert(?:ion)? failed|panic|"
                   r"connection (?:refused|closed)|Traceback)", re.IGNORECASE)
LOCATION_WARNING = re.compile(r"\blocation\.c:\d+> Phone location error: [123]\b")
CLOCK_CHANGE_QUARANTINE = {
    ("release", "basalt"): "test/emulator/clock-change-issue.md",
    ("release", "chalk"): "test/emulator/clock-change-issue.md",
    ("release", "diorite"): "test/emulator/clock-change-issue.md",
    ("debug", "basalt"): "test/emulator/clock-change-issue.md",
    ("debug", "chalk"): "test/emulator/clock-change-issue.md",
}
BASE_SETTINGS = {"CFG_LABEL_MODE": 0, "CFG_SHOW_TIME": 0, "CFG_SHOW_EVENT": 0,
                 "CFG_SHOW_MAG": 0, "CFG_USE_WEATHER": 0, "WX_CLEAR": 1,
                 "CFG_PRECISION": 0, "CFG_BANNER": 0, "CFG_INVERT": 0,
                 "CFG_MIN_ELEV_ENABLED": 0, "CFG_MAX_MAG_ENABLED": 0,
                 "CFG_TINT": 0}
INTERACTIONS = {
    "ui-banner": ("navigation", "sanity"),
    "ui-repeat": ("navigation", "sanity"),
    "ui-flashlight": ("navigation", "sanity"),
    "ui-location-refresh": ("navigation", "sanity"),
    "ui-location-scroll": ("navigation", "sanity"),
    "ui-weather": ("weather", "sanity"),
    "ui-fields": ("display", "comprehensive"),
    "ui-location-error": ("navigation", "comprehensive"),
    "ui-filter-elevation": ("filters", "comprehensive"),
    "ui-filter-no-match": ("filters", "comprehensive"),
    "ui-main-back": ("navigation", "comprehensive"),
}
GROUPS = ("pointing", "clock", "navigation", "weather", "display", "filters")


@dataclass(frozen=True)
class Case:
    name: str
    catalog: int
    object: int
    jd: float
    latitude: float
    longitude: float
    height: int
    refraction: bool
    expected: tuple
    tolerance: float = 0.08
    precise: bool = False
    inverted: bool = False


def command(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, text=True,
                            capture_output=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def catalogs():
    registry = json.loads((ROOT / "catalogs/catalogs.json").read_text())
    return [list(csv.DictReader(line for line in (ROOT / "catalogs" / name).read_text()
                               .splitlines() if not line.startswith("#")))
            for name in registry["catalogs"]]


def host_positions(cases, objects):
    lines = []
    for case in cases:
        obj = objects[case.catalog][case.object]
        kind = KINDS[obj["kind"]] if "kind" in obj else -1
        values = [kind, int(obj.get("index", 0)), case.jd, case.latitude,
                  case.longitude, case.height, int(case.refraction)]
        # Match the catalog pack's fixed-point representation.
        values += [round(float(obj.get(key, 0)) * scale) / scale
                   for key, scale in (("ra_deg", 3600000), ("dec_deg", 3600000),
                                      ("pm_ra_mas_yr", 1), ("pm_dec_mas_yr", 1))]
        lines.append(" ".join(map(str, values)))
    output = command([str(HOST / "pointing_reference"),
                      str(ROOT / "resources/generated/ephemeris.bin")],
                     input="\n".join(lines) + "\n")
    positions = [tuple(map(float, line.split())) for line in output.splitlines()]
    if len(positions) != len(cases) or any(len(p) != 2 for p in positions):
        raise ValueError("Host oracle returned an incomplete result")
    return positions


def reference_cases(tier, objects):
    cases = []
    for i, row in enumerate(csv.reader(io.StringIO(
            command([str(HOST / "test_horizons"), "--references"])))):
        kind, index, jd, lat, lon, height, az, el, refracted = map(float, row)
        obj = next(j for j, obj in enumerate(objects[0])
                   if KINDS[obj["kind"]] == kind and int(obj["index"]) == index)
        cases.append(Case(f"horizons-{i:03}", 0, obj, jd, lat, lon, int(height),
                          bool(refracted), (az, el), 0.17 if kind == 4 else 0.08))
    stars = []
    for i, row in enumerate(csv.reader(io.StringIO(
            command([str(HOST / "test_core"), "--references"])))):
        ra, dec, pm_ra, pm_dec, jd, lat, lon, height, az, el = map(float, row)
        obj = next(j for j, obj in enumerate(objects[1])
                   if float(obj["ra_deg"]) == ra and float(obj["dec_deg"]) == dec
                   and float(obj["pm_ra_mas_yr"]) == pm_ra
                   and float(obj["pm_dec_mas_yr"]) == pm_dec)
        stars.append(Case(f"erfa-{i:03}", 1, obj, jd, lat, lon, int(height),
                          False, (az, el)))
    if tier == "sanity":
        # One of each moving-body category, both hemispheres, a star and a DSO.
        cases = [cases[i] for i in (0, 2, 6, 8, 11, 14)] + [stars[0]]
        baselines = [Case("dso-smoke", 2, 30, 2461041.5, 51.4779, 0, 0, False, ())]
    else:
        cases += stars
        baselines = []
        for site, (lat, lon, height) in enumerate(((51.4779, 0, 0),
                                                  (-30.169, -70.806, 2200),
                                                  (69.65, 18.9553, 30))):
            # Extend the independent vectors with CPU-parity checks for every
            # solar-system entry and representative extended/deep-sky objects.
            for cat, indices in ((0, range(len(objects[0]))), (2, (0, 30, 41, 101))):
                for obj in indices:
                    baselines.append(Case(f"host-{site}-{cat}-{obj}", cat, obj,
                                          2461041.5, lat, lon, height, False, ()))
    for case, position in zip(baselines, host_positions(baselines, objects)):
        cases.append(replace(case, expected=position))
    # Exercise both coordinate font sizes and colour themes on every platform.
    sample = cases[0]
    cases += [replace(sample, name="precise", precise=True),
              replace(sample, name="inverted", inverted=True)]
    return cases


def select_cases(tier, cases, patterns, groups):
    entries = [("live-clock-change", "clock")]
    entries += [(case.name, "display" if case.name in ("precise", "inverted") else "pointing")
                for case in cases]
    entries += [(name, group) for name, (group, minimum) in INTERACTIONS.items()
                if tier == "comprehensive" or minimum == "sanity"]
    for pattern in patterns:
        if not any(fnmatch.fnmatchcase(name, pattern) for name, _ in entries):
            raise ValueError(f"No {tier} case matches {pattern!r}")
    if not patterns and not groups:
        return entries
    selected = [(name, group) for name, group in entries
                if group in groups or any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)]
    if not selected:
        raise ValueError(f"No {tier} cases match the selected groups")
    return selected


def angle_error(actual, expected, circular=False):
    difference = actual - expected
    return abs(math.remainder(difference, 360) if circular else difference)


def fatal_error(log):
    return FATAL.search(LOCATION_WARNING.sub("location warning", log))


def fixture_message(case, timestamp):
    return {**BASE_SETTINGS, "LOC_LAT_E6": round(case.latitude * 1e6),
            "LOC_LON_E6": round(case.longitude * 1e6),
            "LOC_ALT_M": case.height, "LOC_ALT_VALID": 1, "LOC_ACC_M": 0,
            "LOC_SOURCE": 1, "LOC_TIME": timestamp,
            "CFG_REFRACTION": int(case.refraction),
            "CFG_PRECISION": int(case.precise), "CFG_INVERT": int(case.inverted)}


def validate_values(actual, expected, tolerance):
    if len(actual) != 2 or any(not math.isfinite(value) for value in actual):
        raise AssertionError(f"Invalid displayed coordinates: {actual}")
    errors = (angle_error(actual[0], expected[0], True),
              angle_error(actual[1], expected[1]))
    if max(errors) > tolerance:
        raise AssertionError(f"Displayed {actual}; expected {expected}; "
                             f"errors {errors}, limit {tolerance}")


def parse_angles(values, precise):
    digits = 2 if precise else 1
    if any(not re.fullmatch(rf"-?\d+\.\d{{{digits}}}\u00b0", value) for value in values):
        raise AssertionError(f"Wrong coordinate display format: {values}")
    angles = tuple(float(value[:-1]) for value in values)
    if not 0 <= angles[0] < 360 or not -90 <= angles[1] <= 90:
        raise AssertionError(f"Displayed coordinates are out of range: {values}")
    return angles


def supports_precise_display(platform):
    return platform not in LOW_MEMORY_PLATFORMS


def validate_image(path, platform, rows, inverted):
    with Image.open(path) as source:
        image = source.convert("L")
    width, height = SIZES[platform]
    scale = 6
    if image.size != (width * scale, height * scale):
        raise AssertionError(f"Unexpected screenshot size: {image.size}")
    image = image.resize((width, height), Image.Resampling.NEAREST)
    if set(rows) != {0, 1}:
        raise AssertionError(f"Expected two rendered coordinate rows, got {rows}")
    for x, y, w, h in rows.values():
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise AssertionError(f"Coordinate row is clipped: {(x, y, w, h)}")
        if platform in ("chalk", "gabbro"):
            if abs(x + w / 2 - width / 2) > 1:
                raise AssertionError("Round coordinates are not centered")
        elif x + w != width - 4:
            raise AssertionError("Rectangular coordinates are not right-aligned")
        region = image.crop((x, y, x + w, y + h))
        histogram = region.histogram()
        ink = sum(histogram[129:] if inverted else histogram[:128])
        area = region.width * region.height
        if platform in ("chalk", "gabbro"):
            # Empty text-box corners can cross the circle; actual glyphs must not.
            ink = area = 0
            radius = width / 2
            for py in range(h):
                for px in range(w):
                    distance = math.hypot(x + px + 0.5 - radius, y + py + 0.5 - radius)
                    if distance >= radius - 1.5:
                        continue
                    value = region.getpixel((px, py))
                    foreground = value > 128 if inverted else value < 128
                    if foreground and distance > radius - 3:
                        raise AssertionError("Coordinate glyph touches the round display edge")
                    ink += foreground
                    area += 1
        if not area or not 0.05 < ink / area < 0.85:
            raise AssertionError("Coordinate row is blank or filled instead of rendered")
    if rows[0][1] + rows[0][3] >= rows[1][1]:
        raise AssertionError("Coordinate rows overlap")


class Emulator:
    def __init__(self, platform, pbw, output, appinfo):
        self.platform, self.pbw, self.output = platform, pbw, output
        self.keys, self.uuid = appinfo["appKeys"], appinfo["uuid"]
        self.sequence = 0
        self.selection = None
        self.details = None
        self.banner_message = None
        self.reference_timestamp = None
        self.reference_applied_at = None
        self.process = None
        self.log_path = output / "app.log"
        self.log_file = None
        self.commands = (output / "commands.log").open("w")
        self.env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    def args(self, action, *args):
        return ["pebble", action, "--emulator", self.platform, "--vnc", *map(str, args)]

    def run(self, action, *args):
        args = self.args(action, *args)
        self.commands.write(" ".join(args) + "\n")
        self.commands.flush()
        result = subprocess.run(args, cwd=ROOT, env=self.env, text=True,
                                stdout=self.commands, stderr=subprocess.STDOUT,
                                timeout=120)
        self.commands.flush()
        if result.returncode:
            raise RuntimeError(f"{action} failed; see {self.output / 'commands.log'}")

    def screenshot(self, image):
        args = self.args("screenshot", "--scale", 6, "--no-open", image)
        for attempt in range(2):
            self.commands.write(" ".join(args) + "\n")
            self.commands.flush()
            try:
                result = subprocess.run(args, cwd=ROOT, env=self.env, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=120)
            except subprocess.TimeoutExpired as error:
                if error.stdout:
                    output = error.stdout
                    if isinstance(output, bytes):
                        output = output.decode(errors="replace")
                    self.commands.write(output)
                    self.commands.flush()
                raise
            self.commands.write(result.stdout)
            self.commands.flush()
            if not result.returncode:
                return
            if (attempt == 0 and
                    'libpebble2/services/screenshot.py' in result.stdout and
                    re.search(r"^libpebble2\.exceptions\.TimeoutError\s*$",
                              result.stdout, re.MULTILINE)):
                self.commands.write("Retrying screenshot after protocol timeout\n")
                self.commands.flush()
                print(f"{self.platform}: retrying screenshot after protocol timeout", flush=True)
                time.sleep(0.5)
                continue
            raise RuntimeError(f"screenshot failed; see {self.output / 'commands.log'}")

    def message(self, values):
        self.run("send-app-message", "--app-uuid", self.uuid, "--int",
                 *(f"{self.keys[key]}={value}" for key, value in values.items()))

    def logs(self):
        return self.log_path.read_text(errors="replace")

    def wait(self, pattern, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            log = self.logs()
            if fatal_error(log):
                raise AssertionError(f"App/emulator error in {self.log_path}")
            match = re.search(pattern, log)
            if match:
                return match
            if self.process.poll() is not None:
                raise RuntimeError("Emulator log connection exited unexpectedly")
            time.sleep(0.05)
        raise TimeoutError(f"No matching emulator response: {pattern}")

    def start(self):
        self.log_file = self.log_path.open("w")
        self.process = subprocess.Popen(self.args("install", self.pbw, "--logs"),
                                        cwd=ROOT, env=self.env, stdout=self.log_file,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        self.wait(r"App install succeeded", timeout=120)
        time.sleep(1)
        self.configure()

    def configure(self):
        # Back exits the app unless a banner/notice is actually visible.
        screen, _, _ = self.capture(BASE_SETTINGS)
        if screen[0] == "1":
            self.run("emu-button", "click", "back")
            self.capture()

    def set_app_running(self, running):
        action = "Start" if running else "Stop"
        marker = "TEST_APP_START_SENT" if running else "TEST_APP_STOPPED"
        script = f"""def control():
    import time
    from uuid import UUID
    from libpebble2.exceptions import TimeoutError as ProtocolTimeout
    target = UUID({self.uuid!r})
    pebble.send_packet(protocol.AppRunState(data=protocol.AppRunState{action}(uuid=target)))
    if {running!r}:
        print({marker!r})
        return
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            state = pebble.send_and_read(protocol.AppRunState(data=protocol.AppRunStateRequest()), protocol.AppRunState, timeout=5)
        except ProtocolTimeout:
            print("Waiting for app run-state acknowledgement", flush=True)
            continue
        active = isinstance(state.data, protocol.AppRunStateStart) and state.data.uuid == target
        if active == {running!r}:
            print({marker!r})
            return
        time.sleep(0.1)
    raise RuntimeError("App did not reach the requested run state")

control()
"""
        args = self.args("repl")
        self.commands.write(" ".join(args) + f" # app {action.lower()}\n")
        self.commands.flush()
        result = subprocess.run(args, input=script, cwd=ROOT, env=self.env, text=True,
                                capture_output=True, timeout=60)
        self.commands.write(result.stdout + result.stderr)
        self.commands.flush()
        # The SDK REPL exits zero even when an executed statement raises.
        if result.returncode or marker not in result.stdout:
            raise RuntimeError(f"App {action.lower()} failed; see {self.output / 'commands.log'}")

    def capture(self, values=None):
        self.sequence += 1
        self.message({**(values or {}), "TEST_CAPTURE": self.sequence})
        return self.read_capture(self.sequence)

    def read_capture(self, n):
        screen = self.wait(rf"TEST_SCREEN {n} (\d) (\d) (\d) ([^|\n]*)\|([^|\n]*)\|([^\r\n]*)")
        target = self.wait(rf"TEST_TARGET {n} (\d+) (\d+) (\d+)")
        detail = self.wait(rf"TEST_DETAIL {n} (\d) (\d) (\d) ([^|\n]*)\|([^|\n]*)\|"
                           rf"([^|\n]*)\|([^|\n]*)\|([^\r\n]*)")
        self.details = detail.groups()
        self.banner_message = (self.wait(rf"TEST_BANNER {n} ([^\r\n]*)").group(1)
                               if screen.group(1) == "1" else None)
        rows = {int(row[0]): tuple(map(int, row[1:])) for row in
                re.findall(rf"TEST_ROW {n} (\d) (-?\d+) (-?\d+) (\d+) (\d+)", self.logs())}
        selection = tuple(map(int, target.groups()))
        self.selection = selection[:2]
        return screen.groups(), selection, rows

    def quick_interactions(self, actions):
        requests = []
        commands = []
        for action in actions:
            if action[0] == "capture":
                self.sequence += 1
                requests.append(self.sequence)
                commands.append(("capture", self.sequence))
            else:
                commands.append(action)
        script = f"""
import time
from pathlib import Path
from uuid import UUID
from pebble_tool.commands.emucontrol import send_data_to_qemu
from libpebble2.communication.transports.qemu.protocol import QemuButton
from libpebble2.services.appmessage import AppMessageService, Int32
service = AppMessageService(pebble)
buttons = {{"back": QemuButton.Button.Back, "up": QemuButton.Button.Up,
           "select": QemuButton.Button.Select, "down": QemuButton.Button.Down}}
try:
    for action in {commands!r}:
        if action[0] == "capture":
            service.send_message(UUID({self.uuid!r}), {{{self.keys["TEST_CAPTURE"]!r}: Int32(action[1])}})
            deadline = time.monotonic() + 10
            while f"TEST_DETAIL {{action[1]}} " not in Path({str(self.log_path)!r}).read_text(errors="replace"):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Interaction capture did not render")
                time.sleep(0.05)
        elif action[0] == "click":
            send_data_to_qemu(pebble.transport, QemuButton(state=buttons[action[1]]))
            time.sleep(action[2])
            send_data_to_qemu(pebble.transport, QemuButton(state=0))
            time.sleep(0.15)
        elif action[0] == "wait":
            time.sleep(action[1])
        elif action[0] == "assert-stopped":
            state = pebble.send_and_read(
                protocol.AppRunState(data=protocol.AppRunStateRequest()),
                protocol.AppRunState, timeout=5)
            if isinstance(state.data, protocol.AppRunStateStart):
                if state.data.uuid == UUID({self.uuid!r}):
                    raise AssertionError("Back did not exit the app")
            elif not isinstance(state.data, protocol.AppRunStateStop):
                raise AssertionError("Unexpected app run-state response")
        else:
            raise ValueError("Unknown interaction")
finally:
    send_data_to_qemu(pebble.transport, QemuButton(state=0))
    service.shutdown()
print("TEST_INTERACTIONS_COMPLETE")
"""
        args = self.args("repl")
        self.commands.write(" ".join(args) + f" # interactions {commands!r}\n")
        self.commands.flush()
        result = subprocess.run(args, input=f"exec({script!r})\n", cwd=ROOT, env=self.env,
                                text=True, capture_output=True, timeout=60)
        self.commands.write(result.stdout + result.stderr)
        self.commands.flush()
        if result.returncode or "TEST_INTERACTIONS_COMPLETE" not in result.stdout:
            raise RuntimeError(f"Interaction sequence failed; see {self.output / 'commands.log'}")
        captures = []
        for request in requests:
            screen, target, rows = self.read_capture(request)
            captures.append((screen, target, rows, self.details, self.banner_message))
        return captures

    def navigate(self, catalog, obj, objects):
        current_cat, current_obj = self.selection
        for _ in range((catalog - current_cat) % len(objects)):
            self.run("emu-button", "click", "select")
        if current_cat != catalog:
            _, (current_cat, current_obj, _), _ = self.capture()
        if current_cat != catalog:
            raise AssertionError("Catalog button navigation did not reach the target")
        count = len(objects[catalog])
        for _ in range(count):
            down, up = (obj - current_obj) % count, (current_obj - obj) % count
            if not down and not up:
                return
            button, repeat = ("down", down) if down <= up else ("up", up)
            self.run("emu-button", "click", button, "--repeat", repeat, "--interval", 500)
            deadline = time.monotonic() + 30
            previous = current_obj
            while time.monotonic() < deadline:
                _, (current_cat, current_obj, _), _ = self.capture()
                if current_cat != catalog:
                    raise AssertionError("Navigation changed the catalog unexpectedly")
                if current_obj != previous:
                    break
            if current_obj == previous:
                raise AssertionError("Object navigation did not advance")
        if current_obj != obj:
            raise AssertionError("Object navigation did not reach the target")

    def check_clock_change(self, cases, objects):
        case = cases[0]
        self.navigate(case.catalog, case.object, objects)
        _, (_, _, before), _ = self.capture()
        _, (_, _, before), _ = self.capture(fixture_message(case, before))
        changes = []
        steps = [(c, round((c.jd - 2440587.5) * 86400), False) for c in cases[:2]]
        steps += [(cases[1], -120, True), (cases[1], 180 * 86400, True)]
        for case, value, relative in steps:
            self.navigate(case.catalog, case.object, objects)
            timestamp = before + value if relative else value
            self.run("emu-set-time", "--utc", timestamp)
            _, (catalog, obj, observed), _ = self.capture(fixture_message(case, timestamp))
            if (catalog, obj) != (case.catalog, case.object) or abs(observed - timestamp) > 5:
                raise AssertionError("App did not survive/apply a live UTC clock change")
            changes.append({"before": before, "requested": timestamp, "timestamp": observed})
            before = observed
        return {"case": "live-clock-change", "changes": changes}

    def set_reference_time(self, timestamp, live_clock):
        if not live_clock and self.reference_timestamp == timestamp and self.reference_applied_at is not None:
            return self.reference_applied_at
        if not live_clock:
            # Quarantined live-clock regression: set fixtures only with the app stopped.
            self.set_app_running(False)
        self.run("emu-set-time", "--utc", timestamp)
        applied_at = time.monotonic()
        if not live_clock:
            self.set_app_running(True)
            time.sleep(1)
            # A renderer capture confirms readiness; legacy startup state queries can time out.
            self.configure()
            self.reference_timestamp = timestamp
            self.reference_applied_at = applied_at
        return applied_at

    def check(self, case, objects, live_clock=True):
        timestamp = round((case.jd - 2440587.5) * 86400)
        if live_clock:
            self.navigate(case.catalog, case.object, objects)
        applied_at = self.set_reference_time(timestamp, live_clock)
        if not live_clock:
            self.navigate(case.catalog, case.object, objects)
        values = fixture_message(case, timestamp + round(time.monotonic() - applied_at))
        screen, target, rows = self.capture(values)
        deadline = time.monotonic() + 10
        while screen[0] == "1" and time.monotonic() < deadline:
            screen, target, rows = self.capture()
        catalog, obj, observed_time = target
        elapsed = time.monotonic() - applied_at
        if abs(observed_time - (timestamp + elapsed)) > 5:
            raise AssertionError("Emulator did not apply reference UTC time")
        image = self.output / f"{case.name}.png"
        self.screenshot(image)
        banner, inverted, precise, label, az, el = screen
        expected_label = objects[case.catalog][case.object]["id"] or objects[case.catalog][case.object]["name"]
        if (catalog, obj, banner, inverted, precise, label) != (
                case.catalog, case.object, "0", str(int(case.inverted)),
                str(int(case.precise)), expected_label):
            raise AssertionError(f"Wrong screen/selection: {screen}, {catalog}, {obj}")
        displayed = parse_angles((az, el), case.precise)
        # Correct the observed epoch offset using the host model; retain the independent
        # Horizons/ERFA error at the original reference epoch.
        now = replace(case, jd=2440587.5 + observed_time / 86400)
        original, current = host_positions([case, now], objects)
        expected = tuple(ref + delta - base for ref, delta, base in
                         zip(case.expected, current, original))
        validate_values(displayed, expected, case.tolerance)
        validate_image(image, self.platform, rows, case.inverted)
        if fatal_error(self.logs()):
            raise AssertionError("App error detected after screenshot")
        return {"case": case.name, "displayed": displayed, "expected": expected,
                "formatted": [az, el], "label": label, "rows": rows,
                "tolerance": case.tolerance, "timestamp": observed_time}

    def check_interaction(self, name, reference, objects):
        # Reset settings and location so each named case can be run on its own.
        _, (_, _, now), _ = self.capture(BASE_SETTINGS)
        self.capture(fixture_message(reference, now))
        if name == "ui-banner":
            self.capture({"CFG_BANNER": 1})
            previous = self.selection[0]
            captures = self.quick_interactions([
                ("click", "select", 0.1), ("capture",),
                ("click", "back", 0.1), ("capture",),
                ("click", "select", 0.1), ("capture",),
                ("click", "select", 0.9), ("capture",),
            ])
            screen, (catalog, _, _), _, _, _ = captures[0]
            if catalog != (previous + 1) % len(objects) or screen[0] != "1":
                raise AssertionError("Select did not open the next catalog banner")
            if captures[1][0][0] != "0":
                raise AssertionError("Back did not dismiss the banner")
            if captures[2][1][0] != (previous + 2) % len(objects) or captures[2][0][0] != "1":
                raise AssertionError("Select did not reopen the next catalog banner")
            screen, (catalog, _, _), _, _, _ = captures[3]
            if catalog != (previous + 1) % len(objects) or screen[0] != "1":
                raise AssertionError("Long Select on the banner did not select the previous catalog")
        elif name == "ui-repeat":
            self.navigate(1, 0, objects)
            self.capture({"CFG_BANNER": 1})
            captures = self.quick_interactions([
                ("click", "down", 0.1), ("capture",),
                ("click", "down", 1.2), ("capture",),
                ("wait", 0.5), ("capture",),
                ("click", "down", 0.1), ("capture",),
                ("click", "up", 1.2), ("capture",),
            ])
            start = captures[0][1][1]
            catalog, end, _ = captures[1][1]
            if catalog != 1 or not 2 <= (end - start) % len(objects[1]) <= 10:
                raise AssertionError("Holding Down did not repeat object navigation")
            if captures[2][1][1] != end:
                raise AssertionError("Object navigation continued after release")
            reverse_start = captures[3][1][1]
            reverse = captures[4][1][1]
            if captures[4][1][0] != 1 or not 2 <= (reverse_start - reverse) % len(objects[1]) <= 10:
                raise AssertionError("Holding Up did not repeat object navigation")
        elif name == "ui-flashlight":
            self.capture({"CFG_FLASHLIGHT_ENABLED": 1})
            captures = self.quick_interactions([
                ("click", "select", 0.9), ("capture",), ("wait", 1.2),
                ("click", "select", 0.9), ("capture",),
            ])
            for expected, capture in zip(("1", "0"), captures):
                if capture[3][0] != expected:
                    raise AssertionError(f"Long Select did not set flashlight to {expected}")
        elif name == "ui-location-refresh":
            capture = self.quick_interactions([
                ("click", "down", 0.9), ("capture",),
            ])[0]
            if capture[0][0] != "1" or capture[4] != "Updating location...":
                raise AssertionError("Long Down did not show the location refresh notice")
        elif name == "ui-location-scroll":
            self.navigate(0, 0, objects)
            self.capture({"CFG_BANNER": 1, "CFG_SHOW_EVENT": 1,
                          "CFG_MIN_ELEV_ENABLED": 1, "CFG_MIN_ELEV": 0})
            captures = self.quick_interactions([
                ("wait", 2), ("click", "down", 0.9), ("capture",),
                *[("click", "down", 0.1) for _ in range(15)],
                ("wait", 3), ("capture",),
                ("click", "up", 0.1), ("wait", 1), ("capture",),
            ])
            if captures[0][4] != "Updating location...":
                raise AssertionError("Long Down did not trigger a location refresh")
            if captures[1][1][0] != 0 or captures[2][1][0] != 0:
                raise AssertionError("Location refresh scrolling changed the catalog")
            if captures[1][1][1] == captures[2][1][1]:
                raise AssertionError("Object navigation stopped after location refresh")
        elif name == "ui-weather":
            self.navigate(1, 7, objects)
            self.capture({"LOC_LAT_E6": 90000000, "LOC_LON_E6": 0,
                          "LOC_ALT_M": 0, "LOC_ALT_VALID": 1, "LOC_ACC_M": 0,
                          "LOC_SOURCE": 1, "LOC_TIME": now})
            self.capture({"CFG_USE_WEATHER": 1, "CFG_REFRACTION": 1,
                          "CFG_PRECISION": 1, "WX_TEMP_DC": -1000,
                          "WX_TEMP_UNIT": 0, "WX_EXPIRES": now + 3600})
            cold_screen = self.capture()[0]
            if self.details[4] != "-100\u00b0C":
                raise AssertionError(f"Weather temperature missing from screen state: {self.details[4]!r}")
            warm_screen = self.capture({"WX_TEMP_DC": 800, "WX_TEMP_UNIT": 0,
                                        "WX_EXPIRES": now + 3600})[0]
            precise = supports_precise_display(self.platform)
            cold = parse_angles((cold_screen[4], cold_screen[5]), precise)[1]
            warm = parse_angles((warm_screen[4], warm_screen[5]), precise)[1]
            if cold - warm < 0.04:
                raise AssertionError(f"Weather did not affect refraction: cold={cold}, warm={warm}")
            self.capture({"WX_CLEAR": 1})
            if self.details[4]:
                raise AssertionError("Cleared weather is still displayed")
        elif name == "ui-fields":
            self.capture({"CFG_SHOW_TIME": 1, "CFG_SHOW_MAG": 1,
                          "CFG_SHOW_EVENT": 1, "CFG_LABEL_MODE": 1})
            self.navigate(0, 0, objects)
            deadline = time.monotonic() + 20
            while True:
                screen, _, _ = self.capture()
                if self.details[5] or time.monotonic() >= deadline:
                    break
            if not self.details[3] or not self.details[5] or not self.details[6]:
                raise AssertionError("Enabled clock, event, or magnitude is missing from screen state")
            if screen[3] != (objects[0][0]["name"] or objects[0][0]["id"]):
                raise AssertionError("Name label mode was not applied")
            self.capture({"CFG_SHOW_TIME": 0, "CFG_SHOW_MAG": 0, "CFG_SHOW_EVENT": 0})
            if self.details[3] or self.details[5] or self.details[6]:
                raise AssertionError("Disabled clock, event, or magnitude is still displayed")
        elif name == "ui-location-error":
            self.capture({"LOC_ERROR": 2})
            if self.details[2] != "4":
                raise AssertionError("Location error with a saved fix did not show a warning")
            self.capture(fixture_message(reference, now))
            if self.details[2] != "0":
                raise AssertionError("Location warning did not clear after a new fix")
        elif name == "ui-filter-elevation":
            self.navigate(1, 0, objects)
            self.capture({"LOC_LAT_E6": 90000000, "LOC_LON_E6": 0,
                          "LOC_ALT_M": 0, "LOC_ALT_VALID": 1, "LOC_ACC_M": 0,
                          "LOC_SOURCE": 1, "LOC_TIME": now,
                          "CFG_MIN_ELEV_ENABLED": 1, "CFG_MIN_ELEV": 60})
            capture = self.quick_interactions([
                ("click", "down", 0.1), ("wait", 0.8), ("capture",),
            ])[0]
            catalog, obj, _ = capture[1]
            if catalog != 1 or obj != 33:
                raise AssertionError("Elevation filter did not skip lower stars")
            if capture[3][1] == "1":
                self.quick_interactions([("click", "down", 0.1), ("capture",)])
        elif name == "ui-filter-no-match":
            self.navigate(1, 0, objects)
            self.capture({"CFG_MAX_MAG_ENABLED": 1, "CFG_MAX_MAG": -2})
            previous = self.selection
            capture = self.quick_interactions([("click", "down", 0.1), ("capture",)])[0]
            screen, (catalog, obj, _), _, _, banner = capture
            if (catalog, obj) != previous or screen[0] != "1" or \
                    banner != "No objects match filters":
                raise AssertionError("No-match filter did not preserve selection and show a notice")
        elif name == "ui-main-back":
            self.quick_interactions([("click", "back", 0.1), ("wait", 1.0), ("assert-stopped",)])
            self.set_app_running(True)
            time.sleep(1)
            self.configure()
        else:
            raise ValueError(f"Unknown interaction: {name}")
        return {"case": name}

    def close(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)
        if self.log_file:
            self.log_file.close()
        self.commands.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("sanity", "comprehensive"), default="sanity")
    parser.add_argument("--mode", choices=("release", "debug", "both"),
                        help="Default: both for sanity, release for comprehensive")
    parser.add_argument("--platform", action="append", help="Default: every targetPlatforms entry")
    parser.add_argument("--pbw", type=Path, help="Test an already built package (requires one mode)")
    parser.add_argument("--output", type=Path, default=ROOT / "build/emulator-tests")
    parser.add_argument("--case", action="append", default=[], metavar="GLOB",
                        help="Select named cases using shell-style patterns; repeatable")
    parser.add_argument("--group", action="append", choices=GROUPS, default=[],
                        help="Select a case group; repeatable and combined with --case")
    parser.add_argument("--list-cases", action="store_true", help="List selected cases and exit")
    args = parser.parse_args()
    args.mode = args.mode or ("both" if args.tier == "sanity" else "release")
    project = json.loads((ROOT / "package.json").read_text())
    platforms = args.platform or project["pebble"]["targetPlatforms"]
    if any(p not in project["pebble"]["targetPlatforms"] or p not in SIZES for p in platforms):
        parser.error("Platform must be a supported targetPlatforms entry")
    if args.pbw and args.mode == "both":
        parser.error("--pbw requires --mode release or --mode debug")
    if args.list_cases:
        command(["make", "-C", "test/astro", "references"])
        try:
            selected = select_cases(args.tier, reference_cases(args.tier, catalogs()),
                                    args.case, args.group)
        except ValueError as error:
            parser.error(str(error))
        for name, group in selected:
            print(f"{group:12} {name}")
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = {"tier": args.tier, "mode": args.mode, "platforms": platforms,
               "case_patterns": args.case, "groups": args.group,
               "success": False, "started": time.time()}
    try:
        run_suite(args, project, platforms, output)
        summary["success"] = True
    except (Exception, KeyboardInterrupt) as error:
        summary["error"] = str(error) or type(error).__name__
        raise
    finally:
        summary["finished"] = time.time()
        summary["reports"] = [str(path.relative_to(output)) for path in
                              (output / args.tier).glob("*/*/report.json")
                              if path.stat().st_mtime >= summary["started"]]
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def run_suite(args, project, platforms, output):
    command(["make", "-C", "test/astro", "references"])
    objects = catalogs()
    cases = reference_cases(args.tier, objects)
    selected = select_cases(args.tier, cases, getattr(args, "case", []),
                            getattr(args, "group", []))
    selected_names = {name for name, _ in selected}
    pointing = [case for case in cases if case.name in selected_names]
    interactions = [name for name, _ in selected if name in INTERACTIONS]
    modes = ("release", "debug") if args.mode == "both" else (args.mode,)
    failures = []
    for mode in modes:
        if args.pbw:
            pbw = args.pbw.resolve()
        else:
            command(["pebble", "build", "--", f"--mode={mode}"])
            packages = list((ROOT / "build").glob("*.pbw"))
            if len(packages) != 1:
                raise ValueError("Expected exactly one built PBW")
            pbw = output / f"{mode}.pbw"
            shutil.copyfile(packages[0], pbw)
        with zipfile.ZipFile(pbw) as package:
            appinfo = json.loads(package.read("appinfo.json"))
        expected_uuid = (project["pebble"]["uuid"] if mode == "release" else
                         "7a86959b-99d5-4c48-b075-9ae23c165744")
        if appinfo["uuid"] != expected_uuid:
            raise ValueError(f"PBW does not match {mode} identity")
        for platform in platforms:
            directory = output / args.tier / mode / platform
            directory.mkdir(parents=True, exist_ok=True)
            for name in ["failure.png", *(f"{case.name}.png" for case in pointing)]:
                (directory / name).unlink(missing_ok=True)
            emulator = Emulator(platform, pbw, directory, appinfo)
            report = {"tier": args.tier, "mode": mode, "platform": platform,
                      "total": len(selected), "selected": [name for name, _ in selected],
                      "cases": [], "skipped": []}
            quarantine = CLOCK_CHANGE_QUARANTINE.get((mode, platform))
            try:
                emulator.start()
                if "live-clock-change" in selected_names and quarantine:
                    report["skipped"].append({
                        "case": "live-clock-change", "issue": quarantine,
                        "reason": "Known live-clock failure; re-enable this case before closing the bug",
                    })
                    print(f"{mode}/{platform}: SKIPPED live-clock-change ({quarantine})", flush=True)
                elif "live-clock-change" in selected_names:
                    report["active_case"] = "live-clock-change"
                    print(f"{mode}/{platform}: live-clock-change", flush=True)
                    report["cases"].append(emulator.check_clock_change(cases, objects))
                for case in pointing:
                    if case.precise and not supports_precise_display(platform):
                        reason = "0.01-degree display is unavailable on 64 KB platforms"
                        report["skipped"].append({"case": case.name, "reason": reason})
                        print(f"{mode}/{platform}: SKIPPED {case.name} ({reason})", flush=True)
                        continue
                    report["active_case"] = case.name
                    print(f"{mode}/{platform}: {case.name}", flush=True)
                    report["cases"].append(emulator.check(case, objects, live_clock=not quarantine))
                    (directory / f"{case.name}.png").unlink()
                for name in interactions:
                    report["active_case"] = name
                    print(f"{mode}/{platform}: {name}", flush=True)
                    report["cases"].append(emulator.check_interaction(name, cases[0], objects))
                report["success"] = True
                report.pop("active_case", None)
            except KeyboardInterrupt:
                report["success"] = False
                report["error"] = "Interrupted"
                raise
            except Exception as error:
                report["success"] = False
                report["error"] = str(error)
                report["evidence"] = ["app.log", "commands.log", "failure.png"]
                if "active_case" in report:
                    failed_screen = directory / f"{report['active_case']}.png"
                    if failed_screen.exists():
                        report["evidence"].append(failed_screen.name)
                # Preserve the failure while making a best-effort screen capture.
                try:
                    emulator.screenshot(directory / "failure.png")
                except (RuntimeError, subprocess.TimeoutExpired) as capture_error:
                    report["capture_error"] = str(capture_error)
                failures.append(f"{mode}/{platform}: {error}")
                print(f"{mode}/{platform}: FAILED: {error}", flush=True)
            finally:
                report["passed"] = len(report["cases"])
                try:
                    emulator.close()
                finally:
                    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                    if report.get("success"):
                        emulator.log_path.unlink()
                        (directory / "commands.log").unlink()
    if failures:
        raise AssertionError("\n".join(failures))
    print(f"{args.tier}: passed all enabled cases for {len(platforms)} platform(s), {len(modes)} mode(s)")


if __name__ == "__main__":
    main()
