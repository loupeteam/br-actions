#!/usr/bin/env python3
"""
Tests for the ARsim actions' decision logic.

Everything here runs on ANY runner in a couple of seconds: no Automation Studio,
no PVI, no simulator. What is covered is the part that decides things -- log
parsing, path scoping, package resolution, input coercion -- which is where the
bugs that matter live. Actually starting a simulator needs hardware-ish
prerequisites and is covered by the integration job instead.

Run:
    python -m unittest discover -s tests -v
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, relative_path):
    path = os.path.join(REPO_ROOT, relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


start_arsim = load('start_arsim', 'start-arsim/start_arsim.py')
stop_arsim = load('stop_arsim', 'stop-arsim/stop_arsim.py')
build_as_project = load('build_as_project', 'build-as-project/build_as_project.py')


class PlcStatusParsing(unittest.TestCase):
    """The PLCStatus value sits on the line after 'N: PLCStatus' in PVI's log."""

    def mode_from(self, log_text):
        original = start_arsim.run_pil
        start_arsim.run_pil = lambda *a, **k: (0, log_text)
        try:
            return start_arsim.plc_mode('pvitransfer.exe', '127.0.0.1')
        finally:
            start_arsim.run_pil = original

    def test_service(self):
        self.assertEqual(
            self.mode_from('1: Connection\n2: PLCStatus\nService\nPLCStatus SUCCESSFUL'),
            'SERVICE')

    def test_warm_and_cold_start_are_both_run(self):
        # Both mean "booted that way and now running" -- neither is a distinct mode.
        self.assertEqual(self.mode_from('3: PLCStatus\nWarmStart\nPLCStatus SUCCESSFUL'), 'RUN')
        self.assertEqual(self.mode_from('3: PLCStatus\nColdStart\nPLCStatus SUCCESSFUL'), 'RUN')

    def test_crlf_line_endings(self):
        self.assertEqual(
            self.mode_from('12: PLCStatus\r\nBoot\r\nPLCStatus SUCCESSFUL'), 'BOOT')

    def test_unknown_value_passes_through_uppercased(self):
        self.assertEqual(self.mode_from('1: PLCStatus\nSomethingNew\nPLCStatus SUCCESSFUL'),
                         'SOMETHINGNEW')

    def test_no_status_in_log(self):
        self.assertIsNone(self.mode_from('1: Connection\nfailed to connect'))

    def test_pvi_failure_is_not_a_mode(self):
        original = start_arsim.run_pil
        start_arsim.run_pil = lambda *a, **k: (-1, '')
        try:
            self.assertIsNone(start_arsim.plc_mode('pvitransfer.exe', '127.0.0.1'))
        finally:
            start_arsim.run_pil = original


class DirectoryScoping(unittest.TestCase):
    """Both actions scope by install directory so one simulator cannot kill another."""

    def test_matches_own_directory(self):
        process = {'path': os.path.join('C:', os.sep, 'arsim', 'ar000.exe'), 'cmdline': ''}
        self.assertTrue(stop_arsim.in_directory(process, os.path.join('C:', os.sep, 'arsim')))

    def test_rejects_a_different_directory(self):
        process = {'path': os.path.join('C:', os.sep, 'arsim', 'ar000.exe'), 'cmdline': ''}
        self.assertFalse(stop_arsim.in_directory(process, os.path.join('C:', os.sep, 'other')))

    def test_a_sibling_prefix_is_not_a_match(self):
        # 'C:\arsim2' must not be treated as living under 'C:\arsim'.
        process = {'path': os.path.join('C:', os.sep, 'arsim2', 'ar000.exe'), 'cmdline': ''}
        self.assertFalse(stop_arsim.in_directory(process, os.path.join('C:', os.sep, 'arsim')))

    def test_start_and_stop_agree(self):
        process = {'path': os.path.join('C:', os.sep, 'arsim', 'ar000.exe'), 'cmdline': ''}
        root = os.path.join('C:', os.sep, 'arsim')
        self.assertEqual(stop_arsim.in_directory(process, root),
                         start_arsim.in_directory(process, root))


class RucResolution(unittest.TestCase):
    """A wildcard is expected: the CPU folder depends on the target hardware."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def make_package(self, cpu):
        directory = os.path.join(self.root, 'Binaries', 'Intel', cpu, 'RUCPackage')
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, 'RUCPackage.zip')
        open(path, 'w').close()
        return path

    def test_exact_path(self):
        made = self.make_package('CPU1')
        self.assertEqual(start_arsim.resolve_ruc(made), os.path.normpath(made))

    def test_wildcard_single_match(self):
        self.make_package('CPU1')
        pattern = os.path.join(self.root, 'Binaries', 'Intel', '*', 'RUCPackage', 'RUCPackage.zip')
        self.assertTrue(start_arsim.resolve_ruc(pattern).endswith('RUCPackage.zip'))

    def test_wildcard_multiple_matches_is_an_error(self):
        # Ambiguity must not be resolved by picking one: the wrong binary would
        # deploy and the failure would surface much later, as a test failure.
        self.make_package('CPU1')
        self.make_package('CPU2')
        pattern = os.path.join(self.root, 'Binaries', 'Intel', '*', 'RUCPackage', 'RUCPackage.zip')
        with self.assertRaises(SystemExit):
            start_arsim.resolve_ruc(pattern)

    def test_missing_is_an_error(self):
        with self.assertRaises(SystemExit):
            start_arsim.resolve_ruc(os.path.join(self.root, 'nope.zip'))

    def test_build_action_finds_what_start_action_expects(self):
        # The two must agree on the layout, or the actions cannot be chained.
        made = self.make_package('CPU1')
        found = build_as_project.find_ruc_package(self.root, 'Intel')
        self.assertEqual(os.path.normpath(found), os.path.normpath(made))

    def test_build_action_reports_nothing_when_ambiguous(self):
        self.make_package('CPU1')
        self.make_package('CPU2')
        self.assertIsNone(build_as_project.find_ruc_package(self.root, 'Intel'))


class PortProbe(unittest.TestCase):
    def test_open_port_is_detected(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        self.assertTrue(start_arsim.port_open('127.0.0.1', port, timeout=5))

    def test_closed_port_is_not(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(start_arsim.port_open('127.0.0.1', port, timeout=2))


class InputCoercion(unittest.TestCase):
    """A workflow forwarding an unset input passes an empty string.

    argparse's type=/choices= would reject that before the script could apply its
    own default, so every value arrives as a string and is coerced by hand.
    """

    def run_start(self, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'start-arsim', 'start_arsim.py'),
             '--ruc', os.path.join(REPO_ROOT, 'does-not-exist.zip'),
             '--dir', os.path.join(REPO_ROOT, 'unused'), *extra],
            capture_output=True, text=True, timeout=120,
        )

    def test_empty_strings_are_accepted_as_defaults(self):
        # Reaching the "RUC package not found" error proves argument parsing
        # survived the empty values -- an argparse rejection would look different.
        result = self.run_start('--readiness', '', '--opcua-port', '', '--timeout', '',
                                '--stop-existing', '', '--host', '')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('RUC package not found', result.stdout + result.stderr)

    def test_invalid_readiness_is_rejected_clearly(self):
        result = self.run_start('--readiness', 'sideways')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('readiness must be one of', result.stdout + result.stderr)

    def test_non_numeric_timeout_is_rejected_clearly(self):
        result = self.run_start('--timeout', 'soon')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('timeout must be a whole number', result.stdout + result.stderr)


class StopWithNothingRunning(unittest.TestCase):
    def test_exits_zero(self):
        # The cleanup step runs with if:always(), so "nothing to do" must be a
        # success -- otherwise every clean run ends with a red step.
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'stop-arsim', 'stop_arsim.py'),
             '--dir', os.path.join(tempfile.gettempdir(), 'no-arsim-installed-here')],
            capture_output=True, text=True, timeout=120,
        )
        if os.name != 'nt':
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Windows only', result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('No ARsim instances were running', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
