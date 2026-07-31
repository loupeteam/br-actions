#!/usr/bin/env python3
"""
Install a RUC package into an ARsim instance, start it, and wait until it is ready.

ARsim is created through PVITransfer's OfflineCommissioning command, started by
launching ar000loader.exe directly, and then polled until the target answers.

Readiness can be judged two ways, and they answer different questions:

  pvi    PVITransfer's PLCStatus, over ANSL. Authoritative about the AR operating
         mode -- it is the only signal that can tell you the target came up in
         SERVICE (i.e. booted, but user tasks are not running). Needs the ANSL
         port reachable, which is not a given when the runner runs as a Windows
         service: a first-run firewall prompt has nobody to answer it, so the
         rule is silently absent and every query times out.

  opcua  A TCP connect to the OPC UA server port. Proves the server is accepting
         connections, which is usually what a test client actually needs, and it
         is unaffected by the ANSL problem above. Requires the OPC UA server to
         be enabled in the configuration, so it is not universal either.

Default is 'any': whichever answers first wins, and the one that answered is
reported. That is the most likely to work on an unknown runner.

Usage:
    start_arsim.py --ruc <RUCPackage.zip> --dir <arsim dir> [options]

Exit codes:
    0  ARsim is running and reported ready
    1  Creation failed, the loader died, or readiness timed out
"""
import argparse
import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

# Windows process creation flag -- detach the loader from this process, so the
# simulator outlives the step that started it.
DETACHED_PROCESS = 0x00000008

# Every process image that makes up a running ARsim instance.
ARSIM_IMAGES = ('AR000.exe', 'ar000loader.exe')

# B&R installs under more than one root, and which one is in play depends on the
# machine: an image that installs to C:\BrAutomation and a workstation-style
# install under Program Files are both normal. find-as6-build searches the same
# two roots for the same reason.
PVI_INSTALL_ROOTS = (
    r'C:\BrAutomation',
    r'C:\Program Files (x86)\BRAutomation',
    r'C:\Program Files\BRAutomation',
)

# PVI's layout has varied across versions, so match the shapes rather than
# naming one. Sorted descending, so a newer version wins over an older one.
PVI_TRANSFER_PATTERNS = (
    os.path.join('PVI*', 'PVI', 'Tools', 'PVITransfer', 'PVITransfer.exe'),
    os.path.join('PVI', 'V*', 'PVI', 'Tools', 'PVITransfer', 'PVITransfer.exe'),
    os.path.join('PVI*', '**', 'PVITransfer.exe'),
)

# PIL PLCStatus raw value -> AR operating mode. WarmStart/ColdStart both mean
# "booted that way and now running".
PLC_STATUS_MODES = {
    'warmstart': 'RUN',
    'coldstart': 'RUN',
    'service': 'SERVICE',
    'diagnostics': 'DIAG',
    'diagnose': 'DIAG',
    'boot': 'BOOT',
}

MODE_NOTES = {
    'RUN': 'user tasks are running',
    'SERVICE': 'user tasks are NOT running -- the target was stopped, or an '
               'application error such as a cycle time violation forced SERVICE mode',
    'DIAG': 'user tasks are NOT running; only logbook readout, project '
            'installation and restart are possible',
    'BOOT': 'no project installed',
}


def fail(message: str) -> None:
    print(f'::error::{message}', file=sys.stderr)
    sys.exit(1)


def search_pvi_transfer(roots=PVI_INSTALL_ROOTS):
    """Find PVITransfer.exe under the known B&R install roots. None if absent."""
    for root in roots:
        if not os.path.isdir(root):
            continue
        for pattern in PVI_TRANSFER_PATTERNS:
            matches = glob.glob(os.path.join(root, pattern), recursive=True)
            if matches:
                # Descending, so PVI6 beats PVI4 and V4.9 beats V4.8.
                return sorted(matches, reverse=True)[0]
    return None


def resolve_pvi_transfer(explicit: str = '') -> str:
    for candidate in (explicit, os.environ.get('BR_PVI_TRANSFER_PATH', '')):
        if candidate and os.path.isfile(candidate):
            return candidate

    found = search_pvi_transfer()
    if found:
        return found

    fail(
        'PVITransfer.exe not found. Searched: '
        + ', '.join(PVI_INSTALL_ROOTS)
        + '. Install PVI on the runner, set the BR_PVI_TRANSFER_PATH environment '
          'variable, or pass the pvi-transfer input.'
    )


def run_pil(pvi_transfer: str, lines: list, timeout: int = 60):
    """Run a PIL script and return (returncode, log_text).

    PVITransfer prints command *results* (for example the PLCStatus value) only
    to its log file, never to stdout, so the log is what callers must read.
    Never raises on a non-zero exit: the readiness poll expects failures while
    the target is still coming up.
    """
    pil_fd, pil_path = tempfile.mkstemp(prefix='arsim_', suffix='.pil')
    log_fd, log_path = tempfile.mkstemp(prefix='arsim_', suffix='.log')
    os.close(log_fd)
    try:
        with os.fdopen(pil_fd, 'w') as handle:
            handle.write('\n'.join(lines) + '\n')
        try:
            result = subprocess.run(
                [pvi_transfer, '-silent', pil_path, '-' + log_path],
                capture_output=True, text=True, timeout=timeout,
            )
            code = result.returncode
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return -1, ''
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as handle:
                return code, handle.read()
        except OSError:
            return code, ''
    finally:
        for path in (pil_path, log_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def plc_mode(pvi_transfer: str, host: str, timeout: int = 30):
    """Query the AR operating mode. Returns a mode string, or None if unreachable."""
    code, log_text = run_pil(
        pvi_transfer,
        [f'Connection "/IF=tcpip", "/IP={host}", "WT=10"', 'PLCStatus'],
        timeout=timeout,
    )
    if code != 0:
        return None
    # The log shows the result on the line after "N: PLCStatus":
    #   3: PLCStatus
    #   Service
    #   PLCStatus SUCCESSFUL
    lines = log_text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r'^\d+:\s*PLCStatus\s*$', line.strip()):
            for raw in lines[index + 1:]:
                value = raw.strip()
                if value and 'SUCCESSFUL' not in value:
                    return PLC_STATUS_MODES.get(value.lower(), value.upper())
            break
    return None


def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# PowerShell CIM query: pid, image name, and executable path, so a process can be
# matched against an installation directory. tasklist cannot report paths.
CIM_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"{filter}\" | "
    "ForEach-Object {{ \"$($_.ProcessId)|$($_.Name)|$($_.ExecutablePath)|$($_.CommandLine)\" }}"
)


def running_processes():
    """Return [{'pid', 'name', 'path', 'cmdline'}] for every live ARsim process."""
    conditions = ' or '.join(f"Name='{image}'" for image in ARSIM_IMAGES)
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             CIM_QUERY.format(filter=conditions)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []

    processes = []
    for line in result.stdout.strip().splitlines():
        parts = line.strip().split('|', 3)
        if len(parts) < 2:
            continue
        try:
            processes.append({
                'pid': int(parts[0]),
                'name': parts[1],
                'path': parts[2] if len(parts) > 2 else '',
                'cmdline': parts[3] if len(parts) > 3 else '',
            })
        except ValueError:
            continue
    return processes


def in_directory(process: dict, root: str) -> bool:
    root = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    # Compare against root + separator, so a sibling whose name merely EXTENDS the
    # root ("C:\arsim2" against "C:\arsim") is not treated as living inside it.
    # Getting this wrong means stopping somebody else's simulator.
    if not root.endswith(os.sep):
        root += os.sep
    path = os.path.normcase(process.get('path') or '')
    cmdline = os.path.normcase(process.get('cmdline') or '')
    return path.startswith(root) or root in cmdline


def stop_in_dir(arsim_dir: str, verify_timeout: float = 5.0) -> None:
    """Stop the instances installed in this directory, and confirm they are gone.

    Scoped to the directory rather than killing by image name: a machine can run
    several simulators at once (each bound to its own address), and this one is
    only entitled to replace the instance it is about to overwrite. Use the
    stop-arsim action to clear everything.
    """
    targets = [p for p in running_processes() if in_directory(p, arsim_dir)]
    if not targets:
        print(f'No ARsim instance is running in {arsim_dir}.', flush=True)
        return

    for process in targets:
        result = subprocess.run(
            ['taskkill', '/F', '/PID', str(process['pid'])], capture_output=True, text=True
        )
        verb = 'Stopped' if result.returncode == 0 else 'Could not stop'
        print(f"{verb} {process['name']} (pid {process['pid']})", flush=True)

    # Kills are asynchronous; taskkill returning 0 means the signal was accepted,
    # not that the process is gone -- and a survivor keeps the directory locked.
    deadline = time.monotonic() + verify_timeout
    survivors = targets
    while survivors and time.monotonic() < deadline:
        time.sleep(0.5)
        survivors = [p for p in running_processes() if in_directory(p, arsim_dir)]
    if survivors:
        detail = ', '.join(f"{p['name']}(pid {p['pid']})" for p in survivors)
        fail(f'ARsim processes survived being killed: {detail}')

    print(f'Verified: nothing left running in {arsim_dir}.', flush=True)
    # The loader needs a moment to release its files before the directory is refreshed.
    time.sleep(2)


def resolve_ruc(pattern: str) -> str:
    """Resolve the RUC package path, which may contain a wildcard.

    The CPU folder in Binaries/<config>/<cpu>/RUCPackage/ depends on the target
    hardware, so a caller that does not want to hard-code it can pass a '*'.
    """
    pattern = os.path.normpath(os.path.abspath(pattern))
    if not any(ch in pattern for ch in '*?['):
        if not os.path.isfile(pattern):
            fail(f'RUC package not found: {pattern}')
        return pattern
    matches = sorted(glob.glob(pattern))
    if not matches:
        fail(f'No RUC package matched: {pattern}')
    if len(matches) > 1:
        listed = '\n  '.join(matches)
        fail(f'RUC package pattern matched {len(matches)} files, expected one:\n  {listed}')
    return matches[0]


def refresh_dir(path: str) -> None:
    """Empty the directory but keep it.

    Reusing one path means the runner's firewall/UAC registration for
    ar000loader.exe happens once instead of on every deploy.
    """
    if os.path.isdir(path):
        print(f'Refreshing {path}', flush=True)
        for entry in os.listdir(path):
            target = os.path.join(path, entry)
            try:
                shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
            except OSError as error:
                fail(f'Cannot refresh {path} ({error}). Is a simulator still running?')
    else:
        os.makedirs(path, exist_ok=True)


def write_output(name: str, value: str) -> None:
    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as handle:
            handle.write(f'{name}={value}\n')


def diagnose(host: str, opcua_port: int, last_mode: str) -> str:
    """Say which of the look-alike failures this was.

    A loader that died, an application that faulted, and a port a firewall is
    dropping are indistinguishable from the outside without this.
    """
    print('--- diagnostics ---', flush=True)
    procs = running_processes()
    if procs:
        for proc in procs:
            print(f"process : {proc['name']} pid={proc['pid']}", flush=True)
    else:
        print('process : no ARsim processes running', flush=True)
    for port in (opcua_port, 11169):
        state = 'reachable' if port_open(host, port, timeout=2.0) else 'refused/timeout'
        print(f'connect : {host}:{port} {state}', flush=True)
    print('--- end diagnostics ---', flush=True)

    if not procs:
        return (
            'No ARsim process is running -- the loader exited before the runtime came up. '
            'The usual cause is a long installation path: keep the ARsim directory short '
            'and outside the workspace.'
        )
    if last_mode == 'SERVICE':
        return f'The target reported SERVICE -- {MODE_NOTES["SERVICE"]}. Check the System logbook.'
    return (
        'The ARsim process is alive but never reported ready. If the diagnostics above show '
        'the port refusing connections while the process runs, a firewall is most likely '
        'dropping it -- a runner running as a Windows service cannot answer the first-run '
        'firewall prompt, so the rule is never created.'
    )


def start(args) -> None:
    if os.name != 'nt':
        fail('ARsim runs on Windows only; this action requires a Windows runner.')

    # Needed for OfflineCommissioning regardless of which readiness gate is used.
    pvi_transfer = resolve_pvi_transfer(args.pvi_transfer)

    ruc = resolve_ruc(args.ruc)
    arsim_dir = os.path.normpath(os.path.abspath(args.dir))

    print(f'RUC package : {ruc}', flush=True)
    print(f'ARsim dir   : {arsim_dir}', flush=True)
    print(f'PVITransfer : {pvi_transfer}', flush=True)
    print(f'Readiness   : {args.readiness}', flush=True)

    if args.stop_existing:
        # A running instance locks the directory refreshed below.
        stop_in_dir(arsim_dir)
    elif any(in_directory(p, arsim_dir) for p in running_processes()):
        fail(f'An ARsim instance is already running in {arsim_dir} and stop-existing is false.')

    refresh_dir(arsim_dir)

    # Start=0: the loader is launched below instead. Doing it here is deterministic,
    # and avoids OfflineCommissioning reporting a start timeout while a previous
    # instance is still going away.
    print('Creating ARsim instance (OfflineCommissioning)...', flush=True)
    code, log_text = run_pil(
        pvi_transfer,
        [
            f'OfflineCommissioning "{ruc}", "ARSim", "", '
            f'"DestinationDirectory=\'{arsim_dir}\'", "Start=0"'
        ],
        timeout=args.create_timeout,
    )
    if code != 0:
        print(log_text, file=sys.stderr)
        fail(f'OfflineCommissioning failed (PVITransfer exit {code}).')

    loader = os.path.join(arsim_dir, 'ar000loader.exe')
    if not os.path.isfile(loader):
        fail(
            f'ar000loader.exe not found in {arsim_dir} -- OfflineCommissioning '
            'reported success but installed nothing.'
        )

    print(f'Starting ARsim: {loader}', flush=True)
    process = subprocess.Popen(
        [loader], cwd=arsim_dir, creationflags=DETACHED_PROCESS,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    write_output('pid', str(process.pid))
    write_output('arsim-dir', arsim_dir)

    if args.readiness == 'none':
        print('==> ARsim started (readiness check skipped).', flush=True)
        write_output('ready-via', 'none')
        return

    print(f'Waiting up to {args.timeout}s for the target to become ready...', flush=True)
    deadline = time.monotonic() + args.timeout
    consecutive = 0
    last_mode = '(never answered)'
    while time.monotonic() < deadline:
        time.sleep(5)
        ready_via = ''
        mode = None

        if args.readiness in ('opcua', 'any') and port_open(args.host, args.opcua_port):
            ready_via = 'opcua'
        if not ready_via and args.readiness in ('pvi', 'any'):
            mode = plc_mode(pvi_transfer, args.host)
            if mode:
                last_mode = mode
            if mode == 'RUN':
                ready_via = 'pvi'

        if ready_via:
            # Two consecutive successes: a listener is bound part-way through startup,
            # and the operating mode flickers while booting.
            consecutive += 1
            if consecutive >= 2:
                print(f'==> ARsim is ready (via {ready_via}).', flush=True)
                write_output('ready-via', ready_via)
                write_output('mode', last_mode if last_mode != '(never answered)' else '')
                return
        else:
            consecutive = 0
            opcua_state = 'n/a'
            if args.readiness in ('opcua', 'any'):
                opcua_state = 'down'
            print(f'  opcua: {opcua_state}   pvi: {mode or "unreachable"}', flush=True)

    reason = diagnose(args.host, args.opcua_port, last_mode)
    fail(f'ARsim did not become ready within {args.timeout}s. {reason}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create and start an ARsim instance')
    parser.add_argument('--ruc', required=True,
                        help='Path to RUCPackage.zip; may contain a wildcard')
    parser.add_argument('--dir', required=True,
                        help='Directory to install the simulator into')
    # Every value is taken as a string and coerced below. A workflow forwarding an
    # unset input passes an empty string, which argparse's type=/choices= would
    # reject before this script could apply its own default.
    parser.add_argument('--host', default='')
    parser.add_argument('--opcua-port', default='')
    parser.add_argument('--readiness', default='')
    parser.add_argument('--timeout', default='')
    parser.add_argument('--create-timeout', default='')
    parser.add_argument('--pvi-transfer', default='')
    parser.add_argument('--stop-existing', default='')
    args = parser.parse_args()

    def as_int(value, default, name):
        text = str(value or '').strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            fail(f'{name} must be a whole number, got "{text}".')

    args.host = (args.host or '').strip() or '127.0.0.1'
    args.opcua_port = as_int(args.opcua_port, 4840, 'opcua-port')
    args.timeout = as_int(args.timeout, 240, 'timeout')
    args.create_timeout = as_int(args.create_timeout, 180, 'create-timeout')
    args.stop_existing = str(args.stop_existing).strip().lower() not in ('false', '0', 'no')

    args.readiness = (args.readiness or '').strip().lower() or 'any'
    if args.readiness not in ('any', 'opcua', 'pvi', 'none'):
        fail(f'readiness must be one of any, opcua, pvi, none -- got "{args.readiness}".')

    start(args)
