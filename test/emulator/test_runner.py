import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image, ImageDraw

import run


class RunnerTests(unittest.TestCase):
    def test_screenshot_retries_only_protocol_timeout(self):
        timeout = ('  File "libpebble2/services/screenshot.py", line 39, in _read_screenshot\n'
                   'libpebble2.exceptions.TimeoutError\n')
        for outputs, expected_calls, fails in (
                ([(1, timeout), (0, "saved\n")], 2, False),
                ([(1, timeout), (1, timeout)], 2, True),
                ([(1, "other error\n")], 1, True),
                ([(1, "libpebble2.exceptions.TimeoutError\n")], 1, True)):
            with self.subTest(outputs=outputs), tempfile.TemporaryDirectory() as directory:
                emulator = run.Emulator("diorite", Path("test.pbw"), Path(directory),
                                        {"appKeys": {}, "uuid": "test"})
                results = [SimpleNamespace(returncode=code, stdout=output)
                           for code, output in outputs]
                try:
                    with patch.object(run.subprocess, "run", side_effect=results) as command, \
                         patch.object(run.time, "sleep"):
                        if fails:
                            with self.assertRaisesRegex(RuntimeError, "screenshot failed"):
                                emulator.screenshot(Path(directory) / "screen.png")
                        else:
                            emulator.screenshot(Path(directory) / "screen.png")
                    self.assertEqual(command.call_count, expected_calls)
                    log = (Path(directory) / "commands.log").read_text()
                    for _, output in outputs:
                        self.assertIn(output, log)
                    self.assertEqual("Retrying screenshot" in log, expected_calls == 2)
                finally:
                    emulator.close()

    def test_screenshot_process_timeout_preserves_output_without_retrying(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator = run.Emulator("diorite", Path("test.pbw"), Path(directory),
                                    {"appKeys": {}, "uuid": "test"})
            try:
                error = subprocess.TimeoutExpired("pebble", 120, output=b"partial output")
                with patch.object(run.subprocess, "run", side_effect=error) as command, \
                     self.assertRaises(subprocess.TimeoutExpired):
                    emulator.screenshot(Path(directory) / "screen.png")
                self.assertEqual(command.call_count, 1)
                self.assertIn("partial output", (Path(directory) / "commands.log").read_text())
            finally:
                emulator.close()

    def test_angle_tolerances_and_wrap(self):
        run.validate_values((0.0, -12.3), (359.99, -12.31), 0.08)
        for values in ((0.2, -12.3), (0.0, -12.5), (float("nan"), -12.3)):
            with self.subTest(values=values), self.assertRaises(AssertionError):
                run.validate_values(values, (359.99, -12.31), 0.08)

    def test_coordinate_output_shape(self):
        self.assertEqual(run.parse_angles(("179.2\u00b0", "-15.6\u00b0"), False),
                         (179.2, -15.6))
        for values in (("179.2", "15.6"), ("179.20\u00b0", "15.60\u00b0"),
                       ("---.-\u00b0", "15.6\u00b0"), ("360.0\u00b0", "15.6\u00b0")):
            with self.subTest(values=values), self.assertRaises(AssertionError):
                run.parse_angles(values, False)

    def test_precise_display_support_tracks_platform_memory(self):
        for platform in ("basalt", "chalk", "diorite", "flint"):
            self.assertFalse(run.supports_precise_display(platform))
        for platform in ("emery", "gabbro"):
            self.assertTrue(run.supports_precise_display(platform))

    def test_rendering_rejects_blank_clipped_and_misaligned_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            image = Image.new("L", (864, 1008), 255)
            image.save(path)
            rows = {0: (40, 60, 100, 20), 1: (40, 110, 100, 20)}
            with self.assertRaisesRegex(AssertionError, "blank"):
                run.validate_image(path, "basalt", rows, False)
            draw = ImageDraw.Draw(image)
            for x, y, w, h in rows.values():
                draw.rectangle((x * 6, y * 6, (x + w / 2) * 6, (y + h / 2) * 6), fill=0)
            image.save(path)
            run.validate_image(path, "basalt", rows, False)
            for invalid in ({0: rows[0]}, {**rows, 0: (-1, 60, 141, 20)},
                            {**rows, 0: (39, 60, 100, 20)}):
                with self.subTest(rows=invalid), self.assertRaises(AssertionError):
                    run.validate_image(path, "basalt", invalid, False)

    def test_main_layout_checks_spacing_and_separator_thickness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            rows = {0: (40, 65, 100, 20), 1: (40, 116, 100, 20)}
            for inverted in (False, True):
                background, ink = (0, 255) if inverted else (255, 0)
                image = Image.new("L", (144, 168), background)
                draw = ImageDraw.Draw(image)
                draw.line((0, 20, 143, 20), fill=ink)
                draw.line((0, 144, 143, 144), fill=ink)
                draw.rectangle((4, 29, 30, 40), fill=ink)
                draw.rectangle((4, 49, 20, 57), fill=ink)
                draw.rectangle((40, 116, 139, 135), fill=ink)
                image.resize((864, 1008), Image.Resampling.NEAREST).save(path)
                run.validate_main_layout(path, "basalt", rows, inverted)
                draw.line((0, 19, 143, 19), fill=ink)
                image.resize((864, 1008), Image.Resampling.NEAREST).save(path)
                with self.assertRaisesRegex(AssertionError, "one pixel"):
                    run.validate_main_layout(path, "basalt", rows, inverted)
                draw.line((0, 19, 143, 19), fill=background)
                draw.rectangle((4, 29, 30, 40), fill=background)
                draw.rectangle((4, 23, 30, 34), fill=ink)
                image.resize((864, 1008), Image.Resampling.NEAREST).save(path)
                with self.assertRaisesRegex(AssertionError, "Uneven"):
                    run.validate_main_layout(path, "basalt", rows, inverted)

    def test_references_cover_independent_vectors_and_all_solar_entries(self):
        objects = run.catalogs()
        sanity = run.reference_cases("sanity", objects)
        comprehensive = run.reference_cases("comprehensive", objects)
        self.assertEqual(sum(c.name.startswith("horizons-") for c in comprehensive), 66)
        self.assertEqual(sum(c.name.startswith("erfa-") for c in comprehensive), 12)
        self.assertEqual({c.object for c in comprehensive if c.catalog == 0},
                         set(range(len(objects[0]))))
        self.assertTrue({0, 1, 2}.issubset({c.catalog for c in sanity}))
        self.assertTrue(all(c.expected and c.tolerance <= 0.17 for c in comprehensive))
        self.assertGreater(len(comprehensive), len(sanity))

    def test_select_cases_supports_names_globs_and_groups(self):
        cases = [run.Case(name, 0, 0, 2461041.5, 0, 0, 0, False, (0, 0))
                 for name in ("horizons-000", "horizons-001")]
        selected = run.select_cases("sanity", cases, ["horizons-00?"], ["weather"])
        self.assertEqual([name for name, _ in selected],
                         ["horizons-000", "horizons-001", "ui-weather"])
        self.assertIn(("ui-filter-no-match", "filters"),
                      run.select_cases("comprehensive", cases, [], ["filters"]))
        with self.assertRaisesRegex(ValueError, "No sanity case matches"):
            run.select_cases("sanity", cases, ["ui-filter-no-match"], [])
        with self.assertRaisesRegex(ValueError, "No sanity cases"):
            run.select_cases("sanity", cases, [], ["filters"])

    def test_comprehensive_categories_partition_the_full_suite(self):
        cases = run.reference_cases("comprehensive", run.catalogs())
        full_suite = run.select_cases("comprehensive", cases, [], [])
        shards = [run.select_cases("comprehensive", cases, [], [group])
                  for group in run.GROUPS]
        self.assertTrue(all(shards))
        self.assertCountEqual([entry for shard in shards for entry in shard], full_suite)
        for group, shard in zip(run.GROUPS, shards):
            with self.subTest(group=group):
                self.assertEqual({category for _, category in shard}, {group})

    def test_selected_cases_are_the_only_cases_run_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pbw = output / "release.pbw"
            with zipfile.ZipFile(pbw, "w") as archive:
                archive.writestr("appinfo.json", json.dumps({"uuid": "test"}))
            cases = [run.Case("first", 0, 0, 2461041.5, 0, 0, 0, False, (0, 0))]

            class FakeEmulator:
                def __init__(self, platform, pbw, output, appinfo):
                    self.output = output
                    self.log_path = output / "app.log"

                def start(self):
                    self.log_path.write_text("")
                    (self.output / "commands.log").write_text("")

                def check_interaction(self, name, reference, objects):
                    return {"case": name}

                def close(self):
                    pass

            args = argparse.Namespace(pbw=pbw, tier="sanity", mode="release",
                                      case=["ui-weather"], group=[])
            with patch.object(run, "command"), patch.object(run, "catalogs"), \
                 patch.object(run, "reference_cases", return_value=cases), \
                 patch.object(run, "Emulator", FakeEmulator):
                run.run_suite(args, {"pebble": {"uuid": "test"}}, ["emery"], output)
            report = json.loads((output / "sanity/release/emery/report.json").read_text())
            self.assertEqual(report["selected"], ["ui-weather"])
            self.assertEqual(report["total"], 1)
            self.assertEqual(report["cases"], [{"case": "ui-weather"}])

    def test_round_checks_use_glyphs_not_empty_text_box_corners(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "round.png"
            image = Image.new("L", (180, 180), 0)
            draw = ImageDraw.Draw(image)
            draw.ellipse((0, 0, 179, 179), fill=255)
            rows = {0: (20, 70, 140, 20), 1: (20, 135, 140, 20)}
            for _, y, _, _ in rows.values():
                draw.rectangle((65, y, 115, y + 12), fill=0)
            image.resize((1080, 1080), Image.Resampling.NEAREST).save(path)
            run.validate_image(path, "chalk", rows, False)
            draw.rectangle((24, 149, 31, 152), fill=0)
            image.resize((1080, 1080), Image.Resampling.NEAREST).save(path)
            with self.assertRaisesRegex(AssertionError, "round display edge"):
                run.validate_image(path, "chalk", rows, False)

    def test_precise_case_skipped_only_on_low_memory_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pbw = output / "release.pbw"
            with zipfile.ZipFile(pbw, "w") as archive:
                archive.writestr("appinfo.json", json.dumps({"uuid": "test"}))
            cases = [run.Case("precise", 0, 0, 2461041.5, 0, 0, 0, False,
                              (0, 0), precise=True)]

            class FakeEmulator:
                def __init__(self, platform, pbw, output, appinfo):
                    self.output = output
                    self.log_path = output / "app.log"

                def start(self):
                    self.log_path.write_text("")
                    (self.output / "commands.log").write_text("")

                def check(self, case, objects, live_clock=True):
                    (self.output / f"{case.name}.png").write_bytes(b"screen")
                    return {"case": case.name}

                def close(self):
                    pass

            args = argparse.Namespace(pbw=pbw, tier="sanity", mode="release",
                                      case=["precise"], group=[])
            platforms = ["basalt", "chalk", "diorite", "flint", "emery", "gabbro"]
            with patch.object(run, "command"), patch.object(run, "catalogs"), \
                 patch.object(run, "reference_cases", return_value=cases), \
                 patch.object(run, "Emulator", FakeEmulator):
                run.run_suite(args, {"pebble": {"uuid": "test"}}, platforms, output)
            for platform in platforms:
                with self.subTest(platform=platform):
                    report = json.loads((output / f"sanity/release/{platform}/report.json").read_text())
                    self.assertTrue(report["success"])
                    self.assertEqual(report["selected"], ["precise"])
                    self.assertEqual(report["total"], 1)
                    if platform in run.LOW_MEMORY_PLATFORMS:
                        self.assertEqual(report["cases"], [])
                        self.assertEqual(report["passed"], 0)
                        self.assertEqual(report["skipped"][0]["case"], "precise")
                    else:
                        self.assertEqual(report["cases"], [{"case": "precise"}])
                        self.assertEqual(report["passed"], 1)
                        self.assertEqual(report["skipped"], [])

    def test_app_errors_are_not_hidden_by_cli_log_format(self):
        self.assertIsNotNone(run.fatal_error(
            "[12:00:00] pebble_pushto_tool.c:100> ASTRO_ERROR failure"))
        self.assertIsNotNone(run.fatal_error("App crash detected"))
        self.assertIsNone(run.fatal_error("TEST_SCREEN 1 0 0 0 Sun|179.2|15.6"))
        self.assertIsNone(run.fatal_error("[12:00:00] location.c:170> Phone location error: 2"))
        self.assertIsNotNone(run.fatal_error("[12:00:00] location.c:170> ASTRO_ERROR failure"))

    def test_summary_is_written_on_pass_and_setup_failure(self):
        for failure in (None, RuntimeError("build failed"), KeyboardInterrupt()):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                args = ["run.py", "--mode", "release", "--platform", "emery",
                        "--output", directory]
                with patch.object(sys, "argv", args), patch.object(run, "run_suite",
                                                                  side_effect=failure):
                    if failure:
                        with self.assertRaises(type(failure)):
                            run.main()
                    else:
                        run.main()
                summary = json.loads((Path(directory) / "summary.json").read_text())
                self.assertEqual(summary["success"], failure is None)
                if failure:
                    self.assertEqual(summary["error"], str(failure) or type(failure).__name__)

    def test_quarantined_clock_fixture_stops_app_before_changing_time(self):
        emulator = run.Emulator.__new__(run.Emulator)
        emulator.pbw = Path("release.pbw")
        emulator.reference_timestamp = None
        emulator.reference_applied_at = None
        events = []
        with patch.object(emulator, "run", side_effect=lambda *args: events.append(args)), \
             patch.object(emulator, "set_app_running",
                          side_effect=lambda state: events.append(("app", state))), \
             patch.object(emulator, "configure", side_effect=lambda: events.append(("configured",))), \
             patch.object(run.time, "sleep"):
            emulator.set_reference_time(1767268800, live_clock=False)
            emulator.set_reference_time(1767268800, live_clock=False)
        self.assertEqual(events, [
            ("app", False),
            ("emu-set-time", "--utc", 1767268800),
            ("app", True),
            ("configured",),
        ])

    def test_navigation_uses_observed_selection_when_batch_clicks_are_missed(self):
        emulator = run.Emulator.__new__(run.Emulator)
        emulator.selection = (0, 6)
        captures = [((), (0, 4, 0), {}), ((), (0, 0, 0), {})]
        with patch.object(emulator, "run") as commands, \
             patch.object(emulator, "capture", side_effect=captures):
            emulator.navigate(0, 0, [[{}] * 14])
        self.assertEqual([call.args for call in commands.call_args_list], [
            ("emu-button", "click", "up", "--repeat", 6, "--interval", 500),
            ("emu-button", "click", "up", "--repeat", 4, "--interval", 500),
        ])

    def test_interaction_sequence_waits_for_rendered_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            emulator = run.Emulator.__new__(run.Emulator)
            emulator.platform = "emery"
            emulator.uuid = "0f463d47-47e7-46f1-a7d6-94590ffc001d"
            emulator.keys = {"TEST_CAPTURE": 42}
            emulator.sequence = 0
            emulator.details = ()
            emulator.banner_message = None
            emulator.output = Path(directory)
            emulator.log_path = emulator.output / "app.log"
            emulator.log_path.write_text("")
            emulator.env = {}
            emulator.commands = io.StringIO()
            sent = []

            class Button:
                Button = SimpleNamespace(Back=1, Up=2, Select=4, Down=8)

                def __init__(self, state):
                    self.state = state

            class Service:
                def __init__(self, pebble):
                    pass

                def send_message(self, uuid, message):
                    emulator.log_path.write_text(f"TEST_DETAIL {message[42]} ")

                def shutdown(self):
                    pass

            emucontrol = ModuleType("pebble_tool.commands.emucontrol")
            emucontrol.send_data_to_qemu = lambda transport, button: sent.append(button.state)
            qemu = ModuleType("libpebble2.communication.transports.qemu.protocol")
            qemu.QemuButton = Button
            appmessage = ModuleType("libpebble2.services.appmessage")
            appmessage.AppMessageService = Service
            appmessage.Int32 = lambda value: value

            def execute(args, **kwargs):
                output = io.StringIO()
                with redirect_stdout(output):
                    exec(kwargs["input"], {"pebble": SimpleNamespace(transport=None)})
                return subprocess.CompletedProcess(args, 0, output.getvalue(), "")

            modules = {"pebble_tool.commands.emucontrol": emucontrol,
                       "libpebble2.communication.transports.qemu.protocol": qemu,
                       "libpebble2.services.appmessage": appmessage}
            with patch.dict(sys.modules, modules), patch.object(run.subprocess, "run", side_effect=execute), \
                 patch.object(run.time, "sleep"), \
                 patch.object(emulator, "read_capture", return_value=((), (), {})):
                self.assertEqual(len(emulator.quick_interactions([
                    ("click", "select", 0.1), ("capture",),
                ])), 1)
            self.assertEqual(sent, [4, 0, 0])

    def test_app_control_requires_acknowledgement_not_just_repl_exit_zero(self):
        for stdout in ("TEST_APP_STOPPED\n", ""):
            with self.subTest(stdout=stdout):
                emulator = run.Emulator.__new__(run.Emulator)
                emulator.platform = "basalt"
                emulator.uuid = "0f463d47-47e7-46f1-a7d6-94590ffc001d"
                emulator.env = {}
                emulator.output = Path("unused")
                emulator.commands = io.StringIO()
                result = subprocess.CompletedProcess([], 0, stdout, "")
                with patch.object(run.subprocess, "run", return_value=result):
                    if stdout:
                        emulator.set_app_running(False)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "App stop failed"):
                            emulator.set_app_running(False)

    def test_stop_query_timeouts_poll_until_acknowledgement_or_deadline(self):
        exceptions = ModuleType("libpebble2.exceptions")
        exceptions.TimeoutError = TimeoutError
        for acknowledged in (True, False):
            with self.subTest(acknowledged=acknowledged):
                emulator = run.Emulator.__new__(run.Emulator)
                emulator.platform = "basalt"
                emulator.uuid = "0f463d47-47e7-46f1-a7d6-94590ffc001d"
                emulator.env = {}
                emulator.output = Path("unused")
                emulator.commands = io.StringIO()
                protocol = SimpleNamespace(
                    AppRunState=lambda **kwargs: SimpleNamespace(**kwargs),
                    AppRunStateStop=lambda **kwargs: SimpleNamespace(**kwargs),
                    AppRunStateRequest=lambda: None,
                    AppRunStateStart=type("Start", (), {}),
                )
                responses = [TimeoutError(), SimpleNamespace(data=None)] if acknowledged else [TimeoutError()] * 4
                connection = unittest.mock.Mock()
                connection.send_and_read.side_effect = responses

                def execute(args, **kwargs):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exec(kwargs["input"], {"pebble": connection, "protocol": protocol})
                    return subprocess.CompletedProcess(args, 0, output.getvalue(), "")

                with patch.dict(sys.modules, {"libpebble2.exceptions": exceptions}), \
                     patch.object(run.subprocess, "run", side_effect=execute), \
                     patch.object(run.time, "monotonic", side_effect=(0, 1, 6, 11, 16, 21)):
                    if acknowledged:
                        emulator.set_app_running(False)
                        self.assertIn("Waiting for app run-state acknowledgement", emulator.commands.getvalue())
                        self.assertEqual(connection.send_and_read.call_count, 2)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "requested run state"):
                            emulator.set_app_running(False)
                        self.assertEqual(connection.send_and_read.call_count, 4)

    def test_fixture_elapsed_time_is_not_clock_drift(self):
        case = run.Case("fixture", 0, 0, 2461042.0, 0, 0, 0, False, (179.2, 15.6))
        timestamp = round((case.jd - 2440587.5) * 86400)
        objects = [[{"id": "Sun", "name": "Sun"}]]
        for skew in (0, 6):
            with self.subTest(skew=skew):
                emulator = run.Emulator.__new__(run.Emulator)
                emulator.output = Path("unused")
                emulator.platform = "basalt"
                screen = ("0", "0", "0", "Sun", "179.2\u00b0", "15.6\u00b0")
                with patch.object(emulator, "screenshot"), patch.object(emulator, "navigate"), \
                     patch.object(emulator, "set_reference_time", return_value=10), \
                     patch.object(emulator, "capture", return_value=(
                         screen, (0, 0, timestamp + 10 + skew), {})), \
                     patch.object(emulator, "logs", return_value=""), \
                     patch.object(run.time, "monotonic", return_value=20), \
                     patch.object(run, "host_positions", return_value=[case.expected] * 2), \
                     patch.object(run, "validate_image"):
                    if skew:
                        with self.assertRaisesRegex(AssertionError, "reference UTC"):
                            emulator.check(case, objects, live_clock=False)
                    else:
                        emulator.check(case, objects, live_clock=False)

    def test_only_failure_evidence_is_retained(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                pbw = output / "release.pbw"
                with zipfile.ZipFile(pbw, "w") as archive:
                    archive.writestr("appinfo.json", json.dumps({"uuid": "test"}))
                cases = [run.Case(name, 0, 0, 2461041.5, 0, 0, 0, False, (0, 0))
                         for name in ("first", "second")]

                class FakeEmulator:
                    def __init__(self, platform, pbw, output, appinfo):
                        self.platform = platform
                        self.output = output
                        self.log_path = output / "app.log"

                    def start(self):
                        self.log_path.write_text("logs")
                        (self.output / "commands.log").write_text("commands")

                    def check_clock_change(self, case, objects):
                        return {"case": "live-clock-change"}

                    def check(self, case, objects, live_clock=True):
                        (self.output / f"{case.name}.png").write_bytes(b"screen")
                        if failure and self.platform == "emery" and case.name == "second":
                            raise AssertionError("bad pointing")
                        return {"case": case.name, "live_clock": live_clock}

                    def screenshot(self, image):
                        image.write_bytes(b"failure screen")

                    def close(self):
                        pass

                args = argparse.Namespace(pbw=pbw, tier="sanity", mode="release")
                with patch.object(run, "command"), patch.object(run, "catalogs"), \
                     patch.object(run, "reference_cases", return_value=cases), \
                     patch.object(run, "INTERACTIONS", {}), \
                     patch.object(run, "Emulator", FakeEmulator):
                    if failure:
                        with self.assertRaisesRegex(AssertionError, "bad pointing"):
                            run.run_suite(args, {"pebble": {"uuid": "test"}},
                                          ["emery", "flint", "basalt"], output)
                    else:
                        run.run_suite(args, {"pebble": {"uuid": "test"}},
                                      ["emery", "flint", "basalt"], output)
                result = output / "sanity/release/emery"
                report = json.loads((result / "report.json").read_text())
                self.assertEqual(report["success"], not failure)
                self.assertFalse((result / "first.png").exists())
                self.assertEqual((result / "second.png").exists(), failure)
                self.assertEqual((result / "app.log").exists(), failure)
                self.assertEqual((result / "commands.log").exists(), failure)
                flint = output / "sanity/release/flint"
                self.assertTrue(json.loads((flint / "report.json").read_text())["success"])
                self.assertFalse((flint / "app.log").exists())
                basalt = json.loads((output / "sanity/release/basalt/report.json").read_text())
                self.assertTrue(basalt["success"])
                self.assertEqual(basalt["skipped"][0]["case"], "live-clock-change")
                self.assertEqual(basalt["passed"] + len(basalt["skipped"]), basalt["total"])
                self.assertTrue(all(not case["live_clock"] for case in basalt["cases"]))


if __name__ == "__main__":
    unittest.main()
