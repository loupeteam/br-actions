#!/usr/bin/env python3
"""
Stop running ARsim instances, and verify they are actually gone.

Two things this does that a bare `taskkill` does not:

  * It VERIFIES. taskkill returning 0 means the signal was accepted, not that
    the process died. A survivor keeps holding the target's ports, and the next
    run then fails while *deploying* -- which sends you looking at the deploy
    rather than at the cleanup that really failed.

  * It can SCOPE the kill to one installation directory. Killing by image name
    stops every ARsim on the machine, which is what a dedicated runner wants and
    is emphatically not what a shared or interactive machine wants.

Usage:
    stop_arsim.py [--dir <arsim dir>] [--fail-on-survivor true|false]

Exit codes:
    0  Nothing was running, or everything targeted was stopped and verified gone
    1  A process survived (unless --fail-on-survivor false)
"""
import argparse
import os
import subprocess
import sys
import time

# Every process image that makes up a running ARsim instance.
ARSIM_IMAGES = ('AR000.exe', 'ar000loader.exe')

# PowerShell CIM query: image name, pid, and the executable path, so processes
# can be matched against an installation directory. tasklist cannot report paths.
CIM_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"{filter}\" | "
    "ForEach-Object {{ \"$($_.ProcessId)|$($_.Name)|$($_.ExecutablePath)|$($_.CommandLine)\" }}"
)


def query_processes():
    """Return [{'pid', 'name', 'path', 'cmdline'}] for live ARsim processes."""
    conditions = ' or '.join(f"Name='{image}'" for image in ARSIM_IMAGES)
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             CIM_QUERY.format(filter=conditions)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return tasklist_processes()

    if result.returncode != 0:
        return tasklist_processes()

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


def tasklist_processes():
    """Fallback when PowerShell/CIM is unavailable. No path information."""
    processes = []
    for image in ARSIM_IMAGES:
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'IMAGENAME eq {image}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line or 'no tasks' in line.lower():
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == image.lower():
                try:
                    processes.append({'pid': int(parts[1]), 'name': parts[0],
                                      'path': '', 'cmdline': ''})
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


def matching(scope_dir: str):
    processes = query_processes()
    if not scope_dir:
        return processes
    scoped = [p for p in processes if in_directory(p, scope_dir)]
    unscoped = [p for p in processes if p not in scoped]
    if unscoped and any(not p.get('path') for p in unscoped):
        # Without path information there is no way to tell whether an instance
        # belongs to this directory. Say so rather than silently leaving it.
        print(
            '::warning::Could not determine the installation path of every ARsim '
            'process, so some may be left running. Omit arsim-dir to stop all.',
            flush=True,
        )
    return scoped


def stop(scope_dir: str, fail_on_survivor: bool, verify_timeout: float) -> None:
    if os.name != 'nt':
        print('::error::ARsim runs on Windows only; this action requires a Windows runner.',
              file=sys.stderr)
        sys.exit(1)

    targets = matching(scope_dir)
    if not targets:
        where = f' under {scope_dir}' if scope_dir else ''
        print(f'No ARsim instances were running{where}.', flush=True)
        return

    for process in targets:
        result = subprocess.run(
            ['taskkill', '/F', '/PID', str(process['pid'])],
            capture_output=True, text=True,
        )
        verb = 'Stopped' if result.returncode == 0 else 'Could not stop'
        print(f"{verb} {process['name']} (pid {process['pid']})", flush=True)

    # Kills are asynchronous -- poll rather than assuming the exit codes above.
    deadline = time.monotonic() + verify_timeout
    survivors = matching(scope_dir)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.5)
        survivors = matching(scope_dir)

    if survivors:
        detail = ', '.join(f"{p['name']}(pid {p['pid']})" for p in survivors)
        message = (
            f'ARsim processes survived being killed: {detail}. '
            'They still hold the target ports, so the next run will fail to deploy.'
        )
        if fail_on_survivor:
            print(f'::error::{message}', file=sys.stderr)
            sys.exit(1)
        print(f'::warning::{message}', flush=True)
        return

    print('Verified: no ARsim processes remain.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Stop ARsim instances and verify')
    parser.add_argument('--dir', default='',
                        help='Only stop instances installed under this directory')
    # Values arrive as strings and are coerced below: a workflow forwarding an unset
    # input passes an empty string, which argparse's type= would reject before this
    # script could apply its own default.
    parser.add_argument('--fail-on-survivor', default='')
    parser.add_argument('--verify-timeout', default='')
    args = parser.parse_args()

    fail_on_survivor = str(args.fail_on_survivor).strip().lower() not in ('false', '0', 'no')

    timeout_text = str(args.verify_timeout or '').strip()
    try:
        verify_timeout = float(timeout_text) if timeout_text else 10.0
    except ValueError:
        print(f'::error::verify-timeout must be a number, got "{timeout_text}".', file=sys.stderr)
        sys.exit(1)

    stop((args.dir or '').strip(), fail_on_survivor, verify_timeout)
