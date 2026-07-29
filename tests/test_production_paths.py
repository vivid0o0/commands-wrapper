import io
import os
import shlex
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.test_commands_wrapper import cw


def _shell_command(*parts: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


class _FakeCursesError(Exception):
    pass


class _FakePad:
    def __init__(self, *, fail_draw=False, fail_refresh=False):
        self.fail_draw = fail_draw
        self.fail_refresh = fail_refresh
        self.calls = []

    def getmaxyx(self):
        return (200, 200)

    def attron(self, attr):
        self.calls.append(("attron", attr))
        if self.fail_draw:
            raise _FakeCursesError("draw")

    def attroff(self, attr):
        self.calls.append(("attroff", attr))

    def addstr(self, *args):
        self.calls.append(("addstr", *args))
        if self.fail_draw:
            raise _FakeCursesError("draw")

    def addch(self, *args):
        self.calls.append(("addch", *args))
        if self.fail_draw:
            raise _FakeCursesError("draw")

    def refresh(self, *args):
        self.calls.append(("refresh", *args))
        if self.fail_refresh:
            raise _FakeCursesError("refresh")


class _FakeWindow:
    def __init__(self, keys=(), *, height=30, width=100, fail_addstr=False):
        self.keys = list(keys)
        self.height = height
        self.width = width
        self.fail_addstr = fail_addstr
        self.calls = []

    def erase(self):
        self.calls.append(("erase",))

    def getmaxyx(self):
        return (self.height, self.width)

    def addstr(self, *args):
        self.calls.append(("addstr", *args))
        if self.fail_addstr:
            raise _FakeCursesError("addstr")

    def refresh(self):
        self.calls.append(("refresh",))

    def getch(self):
        if self.keys:
            return self.keys.pop(0)
        return 19

    def nodelay(self, enabled):
        self.calls.append(("nodelay", enabled))

    def keypad(self, enabled):
        self.calls.append(("keypad", enabled))

    def timeout(self, value):
        self.calls.append(("timeout", value))


class _FakeCurses:
    error = _FakeCursesError
    KEY_UP = 1001
    KEY_DOWN = 1002
    KEY_LEFT = 1003
    KEY_RIGHT = 1004
    KEY_ENTER = 1005
    KEY_BTAB = 1006
    KEY_BACKSPACE = 1007
    KEY_DC = 1008
    A_BOLD = 0x01
    A_REVERSE = 0x02
    COLOR_WHITE = 7
    COLOR_BLUE = 4

    def __init__(self, *, has_colors=True, fail_colors=False, fail_cursor=False):
        self.has_colors_value = has_colors
        self.fail_colors = fail_colors
        self.fail_cursor = fail_cursor
        self.cursor_calls = []
        self.color_calls = []
        self.pads = []
        self.ungetch_calls = []
        self.wrapper_calls = []

    def has_colors(self):
        return self.has_colors_value

    def start_color(self):
        if self.fail_colors:
            raise self.error("colors")
        self.color_calls.append(("start",))

    def use_default_colors(self):
        self.color_calls.append(("default",))

    def init_pair(self, *args):
        self.color_calls.append(("pair", *args))

    def color_pair(self, number):
        return number << 4

    def curs_set(self, value):
        self.cursor_calls.append(value)
        if self.fail_cursor:
            raise self.error("cursor")
        return 1

    def newpad(self, _height, _width):
        pad = _FakePad()
        self.pads.append(pad)
        return pad

    def ungetch(self, key):
        self.ungetch_calls.append(key)

    def wrapper(self, function):
        self.wrapper_calls.append(function)
        return function(_FakeWindow([3]))


class TuiProductionPathTests(unittest.TestCase):
    def _run_form(self, keys, fields, *, height=30, width=100):
        fake_curses = _FakeCurses()
        window = _FakeWindow(keys, height=height, width=width)
        with mock.patch.object(cw, "curses", fake_curses):
            result = cw.form_input(window, "Form", fields)
        return result, fake_curses, window

    def test_color_and_drawing_helpers_cover_success_and_fail_closed_paths(self):
        fake = _FakeCurses()
        window = _FakeWindow()
        pad = _FakePad()
        with mock.patch.object(cw, "curses", fake):
            cw._init_colors()
            self.assertTrue(fake.color_calls)
            self.assertTrue(cw.SEL())
            self.assertTrue(cw.DIM())
            self.assertTrue(cw.OK())
            self.assertTrue(cw.ERR())
            self.assertTrue(cw.HDR())
            cw._addstr(window, 0, 0, "hello")
            cw._addstr(window, -1, 0, "ignored")
            cw._addstr(window, 0, window.width, "ignored")
            cw._draw_hline(window, 0, 0, 4)
            cw._draw_hline(window, -1, 0, 4)
            cw._draw_field_box_pad(pad, 0, 0, 20, 4, "Title", active=True)
            self.assertGreater(cw._draw_header(window, "Header"), 0)

        with mock.patch.object(cw, "curses", _FakeCurses(has_colors=False)):
            cw._init_colors()
        with mock.patch.object(cw, "curses", _FakeCurses(fail_colors=True)):
            cw._init_colors()
        with mock.patch.object(cw, "curses", _FakeCurses()):
            cw._addstr(_FakeWindow(fail_addstr=True), 0, 0, "ignored")
            cw._draw_field_box_pad(_FakePad(fail_draw=True), 0, 0, 10, 3, "ignored")

    def test_escape_reader_returns_key_and_restores_blocking_mode(self):
        window = _FakeWindow([-1, ord("x")])
        with (
            mock.patch.object(cw.time, "monotonic", side_effect=[0.0, 0.0, 0.01]),
            mock.patch.object(cw.time, "sleep"),
        ):
            self.assertEqual(cw._read_esc_followup_key(window, wait_seconds=0.1), ord("x"))
        self.assertIn(("nodelay", True), window.calls)
        self.assertIn(("nodelay", False), window.calls)

    def test_form_input_saves_values_and_restores_cursor(self):
        fields = [
            cw.Field("name", "Name", value="alpha"),
            cw.Field("body", "Body", value="line one\nline two", multiline=True, box_h=5),
        ]
        result, fake, _window = self._run_form([19], fields)
        self.assertEqual(result, {"name": "alpha", "body": "line one\nline two"})
        self.assertEqual(fake.cursor_calls, [0, 1])
        self.assertTrue(fake.pads)

    def test_form_input_focus_mode_and_navigation_paths(self):
        fields = [
            cw.Field("first", "First", value="ab\ncd", multiline=True, box_h=8),
            cw.Field("second", "Second", value="xy", multiline=True, box_h=8),
        ]
        fields[0].cur_y = 1
        fields[0].cur_x = 1
        keys = [
            _FakeCurses.KEY_LEFT,
            _FakeCurses.KEY_RIGHT,
            _FakeCurses.KEY_UP,
            _FakeCurses.KEY_DOWN,
            9,
            _FakeCurses.KEY_BTAB,
            ord("Z"),
            19,
        ]
        result, _fake, _window = self._run_form(keys, fields, height=14, width=50)
        self.assertIsNotNone(result)
        self.assertIn("Z", result["first"])

    def test_form_input_backspace_delete_and_line_merge_paths(self):
        first = cw.Field("value", "Value", value="ab\ncd", multiline=True)
        first.cur_y = 1
        first.cur_x = 0
        result, _fake, _window = self._run_form([_FakeCurses.KEY_BACKSPACE, 19], [first])
        self.assertEqual(result["value"], "abcd")

        second = cw.Field("value", "Value", value="ab\ncd", multiline=True)
        second.cur_y = 0
        second.cur_x = 2
        result, _fake, _window = self._run_form([_FakeCurses.KEY_DC, 19], [second])
        self.assertEqual(result["value"], "abcd")

        third = cw.Field("value", "Value", value="abc")
        third.cur_x = 2
        result, _fake, _window = self._run_form(
            [_FakeCurses.KEY_BACKSPACE, _FakeCurses.KEY_DC, 19], [third]
        )
        self.assertEqual(result["value"], "a")

    def test_form_input_enter_and_escape_paths(self):
        fields = [cw.Field("first", "First", value="one"), cw.Field("last", "Last", value="two")]
        result, _fake, _window = self._run_form([10, 10], fields)
        self.assertEqual(result, {"first": "one", "last": "two"})

        with mock.patch.object(cw, "_handle_escape_in_form", return_value=False):
            result, _fake, _window = self._run_form([27], [cw.Field("x", "X")])
        self.assertIsNone(result)

        with mock.patch.object(cw, "_handle_escape_in_form", return_value=True):
            result, _fake, _window = self._run_form([27, 19], [cw.Field("x", "X")])
        self.assertEqual(result, {"x": ""})

    def test_form_input_tolerates_cursor_and_pad_errors(self):
        fake = _FakeCurses(fail_cursor=True)
        pad = _FakePad(fail_refresh=True)
        fake.newpad = mock.Mock(return_value=pad)
        window = _FakeWindow([19])
        with mock.patch.object(cw, "curses", fake):
            self.assertEqual(cw.form_input(window, "Form", [cw.Field("x", "X")]), {"x": ""})

    def test_step_labels_and_edit_existing_step_variants(self):
        self.assertIn("COMMAND", cw._step_label({"command": "echo hi"}))
        self.assertIn("SEND", cw._step_label({"send": "text"}))
        self.assertIn("KEY", cw._step_label({"press_key": "enter"}))
        self.assertIn("WAIT", cw._step_label({"wait": 1}))
        self.assertIn("UNKNOWN", cw._step_label({"bad": True}))

        cases = [
            ({"command": "old"}, {"val": "new"}, {"command": "new"}),
            ({"send": "old"}, {"val": "new"}, {"send": "new"}),
            ({"press_key": "tab"}, {"val": "esc"}, {"press_key": "esc"}),
            ({"wait": "1"}, {"val": "2.5"}, {"wait": "2.5"}),
        ]
        for step, form_result, expected in cases:
            with (
                self.subTest(step=step),
                mock.patch.object(cw, "form_input", return_value=form_result),
            ):
                self.assertEqual(cw._edit_existing_step(object(), step), expected)

        with mock.patch.object(cw, "form_input", return_value={"val": "bad"}):
            self.assertIn("_error", cw._edit_existing_step(object(), {"wait": "1"}))
        with mock.patch.object(cw, "form_input", return_value={"val": "-1"}):
            self.assertIn("_error", cw._edit_existing_step(object(), {"wait": "1"}))
        with mock.patch.object(cw, "form_input", return_value=None):
            self.assertIsNone(cw._edit_existing_step(object(), {"command": "old"}))
        self.assertIsNone(cw._edit_existing_step(object(), {"unknown": True}))

    def test_steps_editor_adds_each_step_type_and_saves(self):
        cases = [
            (0, {"val": "echo hi"}, {"command": "echo hi"}),
            (1, {"val": "answer"}, {"send": "answer"}),
            (2, {"val": "tab"}, {"press_key": "tab"}),
            (3, {"val": "1.5"}, {"wait": "1.5"}),
        ]
        for step_type, form_result, expected in cases:
            with (
                self.subTest(step_type=step_type),
                mock.patch.object(cw, "menu", side_effect=[0, step_type, 2]),
                mock.patch.object(cw, "form_input", return_value=form_result),
            ):
                self.assertEqual(cw.steps_editor(object()), [expected])

    def test_steps_editor_handles_invalid_wait_edit_move_delete_and_cancel(self):
        with (
            mock.patch.object(cw, "menu", side_effect=[0, 3, 1]),
            mock.patch.object(cw, "form_input", return_value={"val": "invalid"}),
        ):
            self.assertEqual(cw.steps_editor(object()), [])

        initial = [{"command": "one"}, {"command": "two"}]
        with (
            mock.patch.object(cw, "menu", side_effect=[0, 0, 1, 1, 0, 2, 1, 3, 2]),
            mock.patch.object(cw, "_edit_existing_step", return_value={"command": "edited"}),
        ):
            self.assertEqual(cw.steps_editor(object(), initial), [{"command": "edited"}])

        with mock.patch.object(cw, "menu", return_value=None):
            self.assertIsNone(cw.steps_editor(object(), initial))

    def test_basic_and_curses_wizard_entry_paths(self):
        with mock.patch.object(cw, "print") as print_mock:
            cw._run_basic_wizard("INFO: ready")
        self.assertGreaterEqual(print_mock.call_count, 6)

        with (
            mock.patch.object(cw, "_has_tui_support", return_value=False),
            mock.patch.object(cw, "_run_basic_wizard") as basic_mock,
        ):
            cw.run_wizard("INFO: ready")
        basic_mock.assert_called_once_with(startup_status="INFO: ready")

        fake = _FakeCurses()
        with (
            mock.patch.object(cw, "_has_tui_support", return_value=True),
            mock.patch.object(cw, "curses", fake),
            mock.patch.object(cw, "_wizard_main"),
        ):
            cw.run_wizard()
        self.assertEqual(len(fake.wrapper_calls), 1)

        fake.wrapper = mock.Mock(side_effect=KeyboardInterrupt)
        with (
            mock.patch.object(cw, "_has_tui_support", return_value=True),
            mock.patch.object(cw, "curses", fake),
        ):
            cw.run_wizard()


class WizardMutationPathTests(unittest.TestCase):
    def setUp(self):
        self.window = _FakeWindow()
        self.ui_patches = mock.patch.multiple(
            cw,
            _draw_header=mock.DEFAULT,
            _addstr=mock.DEFAULT,
            ERR=mock.DEFAULT,
            DIM=mock.DEFAULT,
        )
        patched = self.ui_patches.start()
        patched["_draw_header"].return_value = 1
        patched["ERR"].return_value = 0
        patched["DIM"].return_value = 0

    def tearDown(self):
        self.ui_patches.stop()

    def test_wizard_add_recovers_from_invalid_name_and_saves(self):
        with (
            mock.patch.object(
                cw,
                "form_input",
                side_effect=[
                    {"name": "bad!", "desc": "", "timeout": ""},
                    {"name": "good", "desc": "desc", "timeout": "5"},
                ],
            ),
            mock.patch.object(cw, "load_cmds", return_value={}),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "steps_editor", return_value=[{"command": "echo hi"}]),
            mock.patch.object(cw, "_preferred_command_file_for_write", return_value="/tmp/c.yaml"),
            mock.patch.object(cw, "save_cmd", return_value=(True, [])),
            mock.patch.object(cw, "_report_sync_messages", return_value=False),
        ):
            self.assertEqual(cw._wizard_add(self.window), "saved")

    def test_wizard_add_handles_conflict_timeout_and_save_failure(self):
        with (
            mock.patch.object(
                cw,
                "form_input",
                side_effect=[{"name": "Demo", "desc": "", "timeout": ""}, None],
            ),
            mock.patch.object(cw, "load_cmds", return_value={"demo": {}}),
            mock.patch.object(cw, "find_yamls", return_value=[]),
        ):
            self.assertEqual(cw._wizard_add(self.window), "cancelled")

        with (
            mock.patch.object(
                cw,
                "form_input",
                side_effect=[{"name": "demo", "desc": "", "timeout": "0"}, None],
            ),
            mock.patch.object(cw, "load_cmds", return_value={}),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "steps_editor", return_value=[{"command": "echo hi"}]),
        ):
            self.assertEqual(cw._wizard_add(self.window), "cancelled")

        valid = {"name": "demo", "desc": "", "timeout": ""}
        with (
            mock.patch.object(cw, "form_input", side_effect=[valid, valid]),
            mock.patch.object(cw, "load_cmds", return_value={}),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "steps_editor", return_value=[{"command": "echo hi"}]),
            mock.patch.object(cw, "_preferred_command_file_for_write", return_value="/tmp/c.yaml"),
            mock.patch.object(
                cw,
                "save_cmd",
                side_effect=[(False, ["write failed"]), (True, ["sync failed"])],
            ),
            mock.patch.object(cw, "_report_sync_messages", return_value=True),
        ):
            self.assertEqual(cw._wizard_add(self.window), "saved_with_sync_issues")

    def _db(self):
        return {
            "demo": {
                "description": "old",
                "steps": [{"command": "echo old"}],
                "_source": "/tmp/commands.yaml",
            }
        }

    def test_wizard_edit_rename_metadata_steps_and_delete_paths(self):
        renamed_db = self._db()
        renamed_db["renamed"] = renamed_db.pop("demo")
        with (
            mock.patch.object(cw, "load_cmds", side_effect=[self._db(), renamed_db]),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[0, 4]),
            mock.patch.object(cw, "form_input", return_value={"name": "renamed"}),
            mock.patch.object(cw, "rename_in_file", return_value=(True, "", ["sync failed"])),
            mock.patch.object(cw, "_report_sync_messages", return_value=True),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[1, 4]),
            mock.patch.object(cw, "form_input", return_value={"desc": "new", "timeout": "7"}),
            mock.patch.object(cw, "save_cmd", return_value=(True, ["sync failed"])),
            mock.patch.object(cw, "_report_sync_messages", return_value=True),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[2, 4]),
            mock.patch.object(cw, "steps_editor", return_value=[{"command": "echo new"}]),
            mock.patch.object(cw, "save_cmd", return_value=(True, ["sync failed"])),
            mock.patch.object(cw, "_report_sync_messages", return_value=True),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[3, 1]),
            mock.patch.object(cw, "remove_from_file", return_value=(True, "", ["sync failed"])),
            mock.patch.object(cw, "_report_sync_messages", return_value=True),
        ):
            self.assertIn("WARN:", cw._wizard_edit_command(self.window, "demo"))

    def test_wizard_edit_validation_and_persistence_failures(self):
        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[0, 0, 4]),
            mock.patch.object(cw, "form_input", side_effect=[{"name": "bad!"}, None]),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[1, 1, 4]),
            mock.patch.object(
                cw,
                "form_input",
                side_effect=[
                    {"desc": "new", "timeout": "0"},
                    {"desc": "new", "timeout": ""},
                ],
            ),
            mock.patch.object(cw, "save_cmd", return_value=(False, ["write failed"])),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

        with (
            mock.patch.object(cw, "load_cmds", return_value=self._db()),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "menu", side_effect=[3, 1, 4]),
            mock.patch.object(cw, "remove_from_file", return_value=(False, "denied", [])),
        ):
            self.assertEqual(cw._wizard_edit_command(self.window, "demo"), "")

    def test_wizard_main_add_refresh_edit_and_exit(self):
        db = self._db()
        fake = _FakeCurses()
        with (
            mock.patch.object(cw, "curses", fake),
            mock.patch.object(cw, "load_cmds", return_value=db),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "_sync_messages_with_load_warnings", return_value=[]),
            mock.patch.object(cw, "_report_sync_messages", return_value=False),
            mock.patch.object(cw, "menu", side_effect=[0, 2, 1, 3]),
            mock.patch.object(cw, "_wizard_add", return_value="saved"),
            mock.patch.object(cw, "_wizard_edit_command", return_value="OK: edited") as edit_mock,
        ):
            cw._wizard_main(self.window)
        edit_mock.assert_called_once_with(self.window, "demo")
        self.assertIn(("keypad", True), self.window.calls)
        self.assertIn(("timeout", -1), self.window.calls)

    def test_wizard_main_surfaces_initial_sync_and_load_warnings(self):
        fake = _FakeCurses(fail_cursor=True)

        def load_with_warning(_files, warnings=None):
            if warnings is not None:
                warnings.append("bad yaml")
            return {}

        with (
            mock.patch.object(cw, "curses", fake),
            mock.patch.object(cw, "load_cmds", side_effect=load_with_warning),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(
                cw, "_sync_messages_with_load_warnings", return_value=["WARN: conflict"]
            ),
            mock.patch.object(cw, "_report_sync_messages", return_value=False),
            mock.patch.object(cw, "menu", return_value=2) as menu_mock,
        ):
            cw._wizard_main(self.window)
        self.assertIn("WARN:", menu_mock.call_args.args[3])


class ProcessAndExecutionPathTests(unittest.TestCase):
    def test_process_adapter_base_contract_and_timeout(self):
        adapter = cw.ProcessAdapter("echo hi", timeout=None)
        self.assertIsNone(adapter.remaining_timeout())
        adapter.check_timeout()
        for method, args in [
            (adapter.isalive, ()),
            (adapter.interact, ()),
            (adapter.close, ()),
            (adapter.sendline, ()),
            (adapter.send, ("x",)),
            (adapter.returncode, ()),
            (adapter.command_text, ()),
        ]:
            with self.subTest(method=method.__name__), self.assertRaises(NotImplementedError):
                method(*args)

        timed = cw.ProcessAdapter("echo hi", timeout=1)
        timed._deadline = 0.0
        with (
            mock.patch.object(timed, "terminate_for_timeout") as terminate_mock,
            self.assertRaises(cw.StepTimeoutError),
        ):
            timed.check_timeout()
        terminate_mock.assert_called_once_with()

    def test_subprocess_adapter_real_output_input_and_completion(self):
        output_command = _shell_command(cw.sys.executable, "-c", "print('hello', flush=True)")
        adapter = cw.SubprocessProcessAdapter(output_command, timeout=5)
        adapter.interact()
        self.assertEqual(adapter.returncode(), 0)
        self.assertEqual(adapter.command_text(), output_command)
        adapter.close()

        input_command = _shell_command(
            cw.sys.executable,
            "-c",
            "import sys; print(sys.stdin.readline().strip(), flush=True)",
        )
        adapter = cw.SubprocessProcessAdapter(input_command, timeout=5)
        adapter.sendline("answer")
        adapter.interact()
        self.assertEqual(adapter.returncode(), 0)
        adapter.close()

    def test_subprocess_adapter_timeout_send_and_close_error_paths(self):
        class Stream:
            def __init__(self, *, fail=False):
                self.fail = fail
                self.closed = False
                self.writes = []

            def write(self, value):
                if self.fail:
                    raise OSError("write")
                self.writes.append(value)

            def flush(self):
                if self.fail:
                    raise OSError("flush")

            def close(self):
                self.closed = True
                if self.fail:
                    raise OSError("close")

            def __iter__(self):
                return iter(["one\n", "two\n"])

        class Proc:
            def __init__(self):
                self.pid = 99
                self.stdin = Stream()
                self.stdout = Stream()
                self.stderr = Stream(fail=True)
                self.returncode = None
                self.wait_calls = 0

            def poll(self):
                return None if self.returncode is None else self.returncode

            def wait(self, timeout=None):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired("cmd", timeout)
                self.returncode = 0
                return 0

        proc = Proc()
        adapter = cw.SubprocessProcessAdapter.__new__(cw.SubprocessProcessAdapter)
        cw.ProcessAdapter.__init__(adapter, "cmd", timeout=None)
        adapter._proc = proc
        adapter._reader_done = threading.Event()
        adapter._reader_done.set()
        adapter._reader = mock.Mock()

        with (
            mock.patch.object(cw, "_terminate_process_tree") as terminate_mock,
            self.assertRaises(cw.StepTimeoutError),
        ):
            adapter.interact()
        terminate_mock.assert_called()

        proc.returncode = None
        with mock.patch.object(cw, "_terminate_process_tree") as terminate_mock:
            adapter.close()
        self.assertGreaterEqual(terminate_mock.call_count, 1)

        proc.stdin = None
        with self.assertRaises(ValueError):
            adapter.send("x")
        proc.stdin = Stream()
        proc.returncode = 0
        with self.assertRaises(ValueError):
            adapter.send("x")

    @unittest.skipUnless(cw.PEXPECT_AVAILABLE, "pexpect is unavailable")
    def test_pexpect_adapter_close_send_returncode_and_timeout_paths(self):
        class Proc:
            pid = 55
            exitstatus = 3
            signalstatus = None
            logfile_read = None

            def __init__(self):
                self.sent = []
                self.closed = False

            def isalive(self):
                return True

            def close(self):
                self.closed = True

            def sendline(self, value):
                self.sent.append(("line", value))

            def send(self, value):
                self.sent.append(("send", value))

            def interact(self):
                raise OSError("non-interactive")

            def expect(self, *_args, **_kwargs):
                raise cw.pexpect.TIMEOUT("timeout")

        adapter = cw.PExpectProcessAdapter.__new__(cw.PExpectProcessAdapter)
        cw.ProcessAdapter.__init__(adapter, "cmd", timeout=None)
        adapter._proc = Proc()
        adapter._log_sink = cw._PExpectLogSink(io.StringIO())
        adapter._timed_out = threading.Event()
        with mock.patch.object(cw, "_terminate_process_tree") as terminate_mock:
            adapter.close()
        terminate_mock.assert_called_once_with(55)
        adapter.sendline("x")
        adapter.send("y")
        self.assertEqual(adapter.returncode(), 3)
        adapter._proc.exitstatus = None
        adapter._proc.signalstatus = 9
        self.assertEqual(adapter.returncode(), 137)
        adapter._proc.signalstatus = None
        self.assertIsNone(adapter.returncode())
        self.assertEqual(adapter.command_text(), "cmd")

        with (
            mock.patch.object(adapter, "check_timeout"),
            mock.patch.object(adapter, "remaining_timeout", return_value=None),
            mock.patch.object(adapter, "_expire") as expire_mock,
            self.assertRaises(cw.StepTimeoutError),
        ):
            adapter.interact()
        expire_mock.assert_called_once_with()

    def test_pexpect_log_sink_flush_failures_and_binary_buffer(self):
        class Buffer:
            def __init__(self):
                self.data = bytearray()

            def write(self, value):
                self.data.extend(value)

            def flush(self):
                raise OSError("flush")

        class Stream:
            def __init__(self):
                self.buffer = Buffer()

            def write(self, _value):
                raise OSError("write")

            def flush(self):
                raise ValueError("flush")

        stream = Stream()
        sink = cw._PExpectLogSink(stream)
        self.assertEqual(sink.write(b"abc"), 3)
        sink.flush()
        self.assertEqual(bytes(stream.buffer.data), b"abc")

    def test_terminate_process_tree_platform_and_failure_paths(self):
        cw._terminate_process_tree(None)
        cw._terminate_process_tree(-1)
        with (
            mock.patch.object(cw.os, "name", "posix"),
            mock.patch.object(cw.os, "getpgid", return_value=10, create=True),
            mock.patch.object(cw.os, "kill") as kill_mock,
        ):
            cw._terminate_process_tree(20, force=True)
        expected_force_signal = getattr(cw.signal, "SIGKILL", cw.signal.SIGTERM)
        kill_mock.assert_called_once_with(20, expected_force_signal)

        with (
            mock.patch.object(cw.os, "name", "posix"),
            mock.patch.object(cw.os, "getpgid", side_effect=ProcessLookupError, create=True),
        ):
            cw._terminate_process_tree(20)

        with (
            mock.patch.object(cw.os, "name", "nt"),
            mock.patch.object(cw.subprocess, "run") as run_mock,
        ):
            cw._terminate_process_tree(20, force=True)
        self.assertIn("/F", run_mock.call_args.args[0])

        with (
            mock.patch.object(cw.os, "name", "nt"),
            mock.patch.object(cw.subprocess, "run", side_effect=OSError("missing")),
            mock.patch.object(cw.os, "kill") as kill_mock,
        ):
            cw._terminate_process_tree(20)
        kill_mock.assert_called_once()

    def test_change_directory_and_shell_open_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            try:
                cw._change_directory(tmp)
                self.assertTrue(os.path.samefile(os.getcwd(), tmp))
                self.assertTrue(os.path.samefile(os.environ["PWD"], tmp))
                self.assertTrue(os.path.samefile(os.environ["OLDPWD"], before))
            finally:
                os.chdir(before)

        missing = str(Path(tempfile.gettempdir()) / "definitely-missing-commands-wrapper")
        with self.assertRaises(ValueError):
            cw._change_directory(missing)
        with (
            mock.patch.object(cw, "_resolve_cd_target", return_value=tempfile.gettempdir()),
            mock.patch.object(cw.os.path, "isdir", return_value=True),
            mock.patch.object(cw.os, "getcwd", return_value=tempfile.gettempdir()),
            mock.patch.object(cw.os, "chdir", side_effect=OSError("denied")),
            self.assertRaises(ValueError),
        ):
            cw._change_directory("target")

        with (
            mock.patch.object(cw, "_shell_name", return_value="/bin/sh"),
            mock.patch.object(cw.subprocess, "run") as run_mock,
        ):
            cw._open_interactive_shell()
        expected_args = ["/bin/sh"] if os.name == "nt" else ["/bin/sh", "-i"]
        self.assertEqual(run_mock.call_args.args[0], expected_args)

        with (
            mock.patch.object(cw, "_shell_name", return_value="missing"),
            mock.patch.object(cw.subprocess, "run", side_effect=OSError("missing")),
            self.assertRaises(ValueError),
        ):
            cw._open_interactive_shell()

    def test_run_step_all_actions_and_validation_paths(self):
        proc = mock.Mock()
        proc.remaining_timeout.return_value = None
        proc.isalive.return_value = True
        self.assertIs(cw.run_step(proc, {"send": "hello"}, None), proc)
        self.assertIs(cw.run_step(proc, {"press_key": "tab"}, None), proc)
        self.assertIs(cw.run_step(proc, {"press_key": "escape"}, None), proc)
        self.assertIs(cw.run_step(proc, {"press_key": "x"}, None), proc)
        with mock.patch.object(cw.time, "sleep") as sleep_mock:
            self.assertIs(cw.run_step(proc, {"wait": "0"}, None), proc)
        sleep_mock.assert_called_once_with(0.0)

        with self.assertRaises(ValueError):
            cw.run_step(None, {"send": "x"}, None)
        with self.assertRaises(ValueError):
            cw.run_step(None, {"press_key": "x"}, None)
        with self.assertRaises(ValueError):
            cw.run_step(None, {"wait": "bad"}, None)
        with self.assertRaises(ValueError):
            cw.run_step(None, {"wait": -1}, None)
        with self.assertRaises(ValueError):
            cw.run_step(None, {"unknown": True}, None)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cw, "_change_directory") as change,
        ):
            self.assertIsNone(cw.run_step(None, {"command": f"cd {tmp}"}, None))
        change.assert_called_once()

        previous = mock.Mock()
        spawned = mock.Mock()
        with (
            mock.patch.object(cw, "_finalize_process") as finalize,
            mock.patch.object(cw, "_spawn_process", return_value=spawned),
        ):
            self.assertIs(cw.run_step(previous, {"command": "echo hi"}, 2), spawned)
        finalize.assert_called_once_with(previous)

    def test_exec_cmd_error_timeout_failure_finalize_and_single_cd_shell_paths(self):
        with mock.patch.object(cw, "_error"), self.assertRaises(SystemExit):
            cw.exec_cmd("missing", {})
        with mock.patch.object(cw, "_error"), self.assertRaises(SystemExit):
            cw.exec_cmd("bad-timeout", {"steps zero": []})
        with mock.patch.object(cw, "_error"), self.assertRaises(SystemExit):
            cw.exec_cmd("bad-steps", {"steps": "bad"})

        cfg = {"steps": [{"command": "echo hi"}]}
        for error in [
            ValueError("bad"),
            cw.CommandStepFailedError("cmd", 7),
            cw.StepTimeoutError(),
        ]:
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(cw, "run_step", side_effect=error),
                mock.patch.object(cw, "_error"),
                self.assertRaises(SystemExit),
            ):
                cw.exec_cmd("demo", cfg)

        proc = mock.Mock()
        with (
            mock.patch.object(cw, "run_step", return_value=proc),
            mock.patch.object(cw, "_finalize_process", side_effect=cw.StepTimeoutError),
            mock.patch.object(cw, "_error"),
            self.assertRaises(SystemExit),
        ):
            cw.exec_cmd("demo", cfg)

        cd_cfg = {"steps": [{"command": "cd /tmp"}]}
        with (
            mock.patch.object(cw, "run_step", return_value=None),
            mock.patch.object(cw.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cw.sys.stdout, "isatty", return_value=True),
            mock.patch.object(cw, "_open_interactive_shell") as shell_mock,
        ):
            cw.exec_cmd("cd-demo", cd_cfg)
        shell_mock.assert_called_once_with()

        with (
            mock.patch.object(cw, "run_step", return_value=None),
            mock.patch.object(cw.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cw.sys.stdout, "isatty", return_value=True),
            mock.patch.object(cw, "_open_interactive_shell", side_effect=ValueError("bad")),
            mock.patch.object(cw, "_error"),
            self.assertRaises(SystemExit),
        ):
            cw.exec_cmd("cd-demo", cd_cfg)

    def test_followup_after_cd_raw_process_outcomes(self):
        proc = mock.Mock()
        outcomes = [
            cw.CommandStepFailedError("bad", 9),
            cw.StepTimeoutError(),
            ValueError("unknown status"),
        ]
        for outcome in outcomes:
            with (
                self.subTest(outcome=type(outcome).__name__),
                mock.patch.object(cw, "_spawn_process", return_value=proc),
                mock.patch.object(cw, "_finalize_process", side_effect=outcome),
                mock.patch.object(cw, "_error"),
                self.assertRaises(SystemExit),
            ):
                cw._run_followup_after_cd("base", ["raw", "command"], {}, {})

        with (
            mock.patch.object(cw, "_spawn_process", return_value=proc),
            mock.patch.object(cw, "_finalize_process"),
        ):
            cw._run_followup_after_cd("base", ["raw", "command"], {}, {})

        with self.assertRaises(ValueError):
            cw._run_followup_after_cd("base", ["--"], {}, {})


class DirectHelperCoverageTests(unittest.TestCase):
    def test_directory_case_support_is_platform_and_filesystem_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = cw._directory_supports_distinct_case_names(tmp, "posix")
            probe = Path(tmp) / "CaseProbe"
            probe.write_text("probe", encoding="utf-8")
            expected = not (Path(tmp) / "caseprobe").exists()
            probe.unlink()
            self.assertEqual(result, expected)
            self.assertFalse(any("case-probe" in entry for entry in os.listdir(tmp)))

        self.assertFalse(cw._directory_supports_distinct_case_names(tempfile.gettempdir(), "nt"))
        with mock.patch.object(cw.tempfile, "mkstemp", side_effect=OSError("denied")):
            self.assertFalse(
                cw._directory_supports_distinct_case_names(tempfile.gettempdir(), "posix")
            )

    def test_first_launch_marker_path_uses_user_config_directory(self):
        with mock.patch.object(cw, "_user_config_dir", return_value="/config/root"):
            self.assertEqual(
                cw._first_launch_tip_marker_path(),
                os.path.join("/config/root", ".first-launch-tip-shown"),
            )

    def test_has_tui_support_requires_curses_and_both_ttys(self):
        with (
            mock.patch.object(cw, "CURSES_AVAILABLE", True),
            mock.patch.object(cw.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cw.sys.stdout, "isatty", return_value=True),
        ):
            self.assertTrue(cw._has_tui_support())
        with (
            mock.patch.object(cw, "CURSES_AVAILABLE", False),
            mock.patch.object(cw.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cw.sys.stdout, "isatty", return_value=True),
        ):
            self.assertFalse(cw._has_tui_support())

    def test_parser_exit_emits_message_and_status(self):
        parser = cw._Parser(add_help=False)
        with (
            mock.patch.object(parser, "_print_message") as print_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            parser.exit(7, "controlled exit\n")
        self.assertEqual(exc.exception.code, 7)
        print_mock.assert_called_once_with("controlled exit\n", cw.sys.stderr)


class StateCliAndPlatformPathTests(unittest.TestCase):
    def test_wrapper_context_validation_pruning_clear_and_apply(self):
        context = {
            "bad-entry": "nope",
            "empty-cwd": {"cwd": "", "expires_at": 999},
            "bad-expiry-type": {"cwd": "/tmp", "expires_at": []},
            "bad-expiry-value": {"cwd": "/tmp", "expires_at": "never"},
            "expired": {"cwd": "/tmp", "expires_at": 1},
            "valid": {"cwd": "/tmp", "expires_at": 999},
        }
        cw._prune_wrapper_cwd_context(context, now=10)
        self.assertEqual(context, {"valid": {"cwd": "/tmp", "expires_at": 999}})

        with tempfile.TemporaryDirectory() as tmp:
            context_path = Path(tmp) / "state" / "cwd.yaml"
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            before = os.getcwd()
            try:
                with mock.patch.object(
                    cw, "_wrapper_cwd_context_path", return_value=str(context_path)
                ):
                    cw._save_wrapper_cwd_context(
                        {"123": {"cwd": str(second), "expires_at": cw.time.time() + 60}},
                        str(context_path),
                    )
                    loaded = cw._load_wrapper_cwd_context(str(context_path))
                    self.assertTrue(os.path.samefile(loaded["123"]["cwd"], second))

                    os.chdir(first)
                    cw._apply_wrapper_cwd_context(123)
                    self.assertTrue(os.path.samefile(os.getcwd(), second))
                    self.assertTrue(os.path.samefile(os.environ["OLDPWD"], first))
                    self.assertTrue(os.path.samefile(os.environ["PWD"], second))
                    self.assertIsNone(cw._peek_wrapper_cwd_context(123))

                    cw._save_wrapper_cwd_context(
                        {"456": {"cwd": str(first), "expires_at": cw.time.time() + 60}},
                        str(context_path),
                    )
                    cw._clear_wrapper_cwd_context(456)
                    self.assertIsNone(cw._peek_wrapper_cwd_context(456))
                    cw._clear_wrapper_cwd_context(0)
                    cw._apply_wrapper_cwd_context(None)
            finally:
                os.chdir(before)

    def test_wrapper_context_fail_closed_storage_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "context.yaml")
            Path(path).write_text("- not-a-mapping\n", encoding="utf-8")
            self.assertEqual(cw._load_wrapper_cwd_context(path), {})
            Path(path).write_text(
                "bad: {cwd: '', expires_at: 2}\n"
                "bad2: {cwd: /tmp, expires_at: []}\n"
                "bad3: {cwd: /tmp, expires_at: never}\n"
                "good: {cwd: /tmp, expires_at: '2'}\n",
                encoding="utf-8",
            )
            self.assertEqual(set(cw._load_wrapper_cwd_context(path)), {"good"})

            cw._save_wrapper_cwd_context({}, path)
            self.assertFalse(Path(path).exists())
            with mock.patch.object(cw, "_atomic_write_text", side_effect=OSError("denied")):
                cw._save_wrapper_cwd_context({"1": {"cwd": "/tmp", "expires_at": 2}}, path)

        for function in (
            cw._remember_wrapper_cwd_context,
            cw._consume_wrapper_cwd_context,
            cw._peek_wrapper_cwd_context,
            cw._clear_wrapper_cwd_context,
        ):
            with (
                self.subTest(function=function.__name__),
                mock.patch.object(cw, "_exclusive_file_lock", side_effect=OSError("locked")),
            ):
                result = (
                    function(1, "/tmp")
                    if function is cw._remember_wrapper_cwd_context
                    else function(1)
                )
                if function in (cw._consume_wrapper_cwd_context, cw._peek_wrapper_cwd_context):
                    self.assertIsNone(result)

    def test_menu_empty_status_scroll_and_quit_paths(self):
        fake = _FakeCurses()
        with mock.patch.object(cw, "curses", fake):
            self.assertIsNone(cw.menu(_FakeWindow(), "Empty", []))
            for status in ("OK: fine", "WARN: warning", "INFO: note", "ERR: bad"):
                with self.subTest(status=status):
                    window = _FakeWindow(
                        [fake.KEY_DOWN, fake.KEY_DOWN, fake.KEY_UP, ord("q")], height=8
                    )
                    self.assertIsNone(
                        cw.menu(window, "Menu", [f"item-{i}" for i in range(12)], status=status)
                    )

    def test_shell_path_message_and_stdin_helpers(self):
        with (
            mock.patch.object(cw.os, "name", "nt"),
            mock.patch.dict(os.environ, {"COMSPEC": "custom-cmd.exe"}, clear=False),
        ):
            self.assertEqual(cw._shell_name(), "custom-cmd.exe")
        with (
            mock.patch.object(cw.os, "name", "posix"),
            mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}, clear=False),
        ):
            self.assertEqual(cw._shell_name(), "/bin/zsh")

        self.assertIsNone(cw._first_non_warning_message([]))
        self.assertEqual(cw._first_non_warning_message(["WARN: only"]), "WARN: only")
        self.assertEqual(cw._first_non_warning_message(["WARN: first", "failure"]), "failure")

        with mock.patch.object(cw.sys, "stdin", io.StringIO("demo: value\n")):
            self.assertEqual(cw._read_bounded_yaml_stdin(), "demo: value\n")

        class InvalidInput:
            buffer = io.BytesIO(b"\xff")

        with mock.patch.object(cw.sys, "stdin", InvalidInput()), self.assertRaises(ValueError):
            cw._read_bounded_yaml_stdin()

        class OversizedInput:
            buffer = io.BytesIO(b"x" * (cw.MAX_COMMAND_FILE_BYTES + 1))

        with mock.patch.object(cw.sys, "stdin", OversizedInput()), self.assertRaises(ValueError):
            cw._read_bounded_yaml_stdin()

    def test_cd_resolution_and_command_shape_helpers(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            cw._resolve_cd_target("-")
        previous = tempfile.gettempdir()
        with mock.patch.dict(os.environ, {"OLDPWD": previous}, clear=False):
            self.assertEqual(
                os.path.realpath(cw._resolve_cd_target("-")), os.path.realpath(previous)
            )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"CW_TEST_ROOT": tmp}, clear=False),
        ):
            reference = "%CW_TEST_ROOT%" if os.name == "nt" else "$CW_TEST_ROOT"
            self.assertEqual(
                os.path.realpath(cw._resolve_cd_target(reference)), os.path.realpath(tmp)
            )

        self.assertIsNone(cw._extract_cd_target(123))
        self.assertIsNone(cw._extract_cd_target("cd 'unterminated"))
        self.assertEqual(cw._extract_cd_target("cd"), "~")
        self.assertIsNone(cw._extract_cd_target("cd one two"))
        self.assertFalse(cw._is_single_cd_step([]))
        self.assertFalse(cw._is_single_cd_step(["cd /tmp"]))

    def test_print_list_and_package_source_paths(self):
        with mock.patch.object(cw, "print") as print_mock:
            cw.print_list({})
        self.assertTrue(any("No commands" in str(call) for call in print_mock.call_args_list))

        with mock.patch.object(cw, "print") as print_mock:
            cw.print_list(
                {
                    "zeta": {"description": "last"},
                    "alpha": {"description": "first"},
                }
            )
        rendered = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertLess(rendered.index("alpha"), rendered.index("zeta"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "one" / "two" / "commands-wrapper"
            script.parent.mkdir(parents=True)
            script.touch()
            (root / "pyproject.toml").touch()
            with mock.patch.object(cw, "__file__", str(script)):
                resolved = cw._find_package_source()
            self.assertIsNotNone(resolved)
            self.assertTrue(os.path.samefile(str(resolved), root))

        with (
            mock.patch.object(cw, "__file__", "/missing/a/b/commands-wrapper"),
            mock.patch.object(cw, "_SCRIPT_DIR", "/missing/source"),
            mock.patch.object(cw.os.path, "isfile", return_value=False),
        ):
            self.assertIsNone(cw._find_package_source())

    def test_pip_install_and_uninstall_dispatch_paths(self):
        with (
            mock.patch.object(cw, "_find_package_source", return_value=None),
            mock.patch.object(cw, "_error") as error_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            cw._pip_install()
        self.assertEqual(exc.exception.code, 1)
        error_mock.assert_called_once()

        with (
            mock.patch.object(cw, "_find_package_source", return_value="/project"),
            mock.patch.object(cw, "_pip_install_scope_args", return_value=["--user"]),
            mock.patch.object(cw, "_run_pip", return_value=7) as run_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            cw._pip_install()
        self.assertEqual(exc.exception.code, 7)
        run_mock.assert_called_once_with(["install", "--user", "/project"], cwd="/project")

        with (
            mock.patch.object(cw, "sync_binaries", return_value=["sync warning"]),
            mock.patch.object(cw, "_report_sync_messages") as report_mock,
            mock.patch.object(cw, "_run_pip", return_value=1),
            mock.patch.object(cw, "_warn") as warn_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            cw._pip_uninstall()
        self.assertEqual(exc.exception.code, 0)
        report_mock.assert_called_once_with(["sync warning"])
        warn_mock.assert_called_once()

        with (
            mock.patch.object(cw, "sync_binaries", return_value=[]),
            mock.patch.object(cw, "_report_sync_messages"),
            mock.patch.object(cw, "_run_pip", side_effect=[0, 0]) as run_mock,
            mock.patch.object(cw, "_ok") as ok_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            cw._pip_uninstall()
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(run_mock.call_count, 2)
        ok_mock.assert_called_once()

    def test_cmd_add_yaml_rejects_malformed_and_invalid_batches(self):
        early_cases = [
            ("[not: valid", "YAML parse error"),
            ("- list-item\n", "Input must be a YAML mapping"),
        ]
        for payload, expected in early_cases:
            with (
                self.subTest(payload=payload),
                mock.patch.object(cw, "_error") as error_mock,
                self.assertRaises(SystemExit) as exc,
            ):
                cw.cmd_add_yaml(payload)
            self.assertEqual(exc.exception.code, 1)
            self.assertIn(expected, error_mock.call_args.args[0])

        with (
            mock.patch.object(cw, "_preferred_command_file_for_write", return_value="/tmp/c.yaml"),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "load_cmds", return_value={"Demo": {}, "demo": {}}),
            mock.patch.object(
                cw,
                "_build_command_lookup_index",
                return_value=({}, ["case-insensitive collision"]),
            ),
            mock.patch.object(cw, "_error") as error_mock,
            self.assertRaises(SystemExit),
        ):
            cw.cmd_add_yaml("demo:\n  steps:\n    - command: echo hi\n")
        self.assertTrue(any("collision" in call.args[0] for call in error_mock.call_args_list))

        payload = (
            "bad!: {steps: [{command: echo hi}]}\n"
            "not_mapping: value\n"
            "bad_config: {description: missing steps}\n"
        )
        with (
            mock.patch.object(cw, "_preferred_command_file_for_write", return_value="/tmp/c.yaml"),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "load_cmds", return_value={}),
            mock.patch.object(cw, "_error") as error_mock,
            self.assertRaises(SystemExit) as exc,
        ):
            cw.cmd_add_yaml(payload)
        self.assertEqual(exc.exception.code, 1)
        self.assertTrue(
            any("No valid commands" in call.args[0] for call in error_mock.call_args_list)
        )

        with (
            mock.patch.object(cw, "_preferred_command_file_for_write", return_value="/tmp/c.yaml"),
            mock.patch.object(cw, "find_yamls", return_value=[]),
            mock.patch.object(cw, "load_cmds", return_value={}),
            mock.patch.object(cw, "save_cmd", return_value=(False, ["write failed"])),
            mock.patch.object(cw, "_report_sync_messages") as report_mock,
            mock.patch.object(cw, "_error"),
            self.assertRaises(SystemExit),
        ):
            cw.cmd_add_yaml("demo:\n  steps:\n    - command: echo hi\n")
        report_mock.assert_called_once_with(["write failed"])

    def test_sync_binaries_platform_error_and_reconciliation_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_file = Path(tmp) / "not-a-directory"
            target_file.write_text("x", encoding="utf-8")
            errors = cw.sync_binaries({}, uninstall=True, bin_dir=str(target_file))
            self.assertTrue(any("not a directory" in message for message in errors))

        with mock.patch.object(cw.os, "makedirs", side_effect=OSError("denied")):
            errors = cw.sync_binaries({}, bin_dir="/uncreatable")
        self.assertTrue(any("failed to create" in message for message in errors))

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"PATH": ""}, clear=False),
        ):
            errors = cw.sync_binaries(
                {"Demo": {"steps": [{"command": "echo hi"}]}},
                bin_dir=tmp,
                platform_name="nt",
                report_conflicts=False,
            )
            self.assertFalse(errors)
            self.assertTrue((Path(tmp) / "demo.cmd").is_file())
            self.assertTrue((Path(tmp) / "demo.ps1").is_file())
            self.assertNotIn("Demo.cmd", os.listdir(tmp))

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cw.os, "listdir", side_effect=OSError("denied")):
                errors = cw.sync_binaries({}, bin_dir=tmp, report_conflicts=False)
            self.assertTrue(any("failed to list" in message for message in errors))

            stale = Path(tmp) / "stale"
            stale.write_text(f"# {cw.WRAPPER_MARKER}\n", encoding="utf-8")
            with mock.patch.object(cw.os, "remove", side_effect=OSError("denied")):
                errors = cw.sync_binaries({}, bin_dir=tmp, report_conflicts=False)
            self.assertTrue(any("failed to reconcile" in message for message in errors))

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cw, "_atomic_write_text", side_effect=OSError("denied")),
        ):
            errors = cw.sync_binaries({}, bin_dir=tmp, report_conflicts=False)
        self.assertTrue(any("failed to write wrapper" in message for message in errors))


if __name__ == "__main__":
    unittest.main()
