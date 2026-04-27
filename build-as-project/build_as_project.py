#!/usr/bin/env python3
"""
Build a B&R Automation Studio project for a single configuration.

BR.AS.Build.exe exits with code 1 on warnings-only builds.
This script only fails (exits non-zero) when actual build errors are reported.

Usage:
    build_as_project.py --exe <path> --project <apj> --config <name> [--build-mode Build|Rebuild]

Exit codes:
    0  Build succeeded (errors == 0, warnings may be present)
    1  Build failed (one or more errors reported)
"""
import argparse
import re
import subprocess
import sys


def build(exe_path: str, project_apj: str, config: str, build_mode: str = 'Build') -> None:
    cmd = [exe_path, project_apj, '-c', config, '-buildMode', build_mode]
    print(f'Running: {" ".join(cmd)}', flush=True)

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    stdout_lines = []
    stderr_lines = []

    # Stream stdout line-by-line as the build runs
    for line in process.stdout:
        print(line, end='', flush=True)
        stdout_lines.append(line)

    # Drain stderr after stdout closes
    for line in process.stderr:
        print(line, end='', file=sys.stderr, flush=True)
        stderr_lines.append(line)

    process.wait()

    combined = ''.join(stdout_lines) + ''.join(stderr_lines)

    # BR.AS.Build.exe reports errors as "N error(s)" in its summary line.
    # Exit code 1 alone is not sufficient (it also appears on warnings-only builds).
    error_match = re.search(r'\b([1-9]\d*) error\(s\)', combined)
    if error_match:
        print(
            f'::error::Build failed: {error_match.group(1)} error(s) reported for config "{config}"',
            file=sys.stderr,
        )
        sys.exit(1)

    print(f'Build completed successfully for config: {config}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build an AS6 project configuration')
    parser.add_argument('--exe',        required=True,  help='Path to BR.AS.Build.exe')
    parser.add_argument('--project',    required=True,  help='Path to the .apj file')
    parser.add_argument('--config',     required=True,  help='Configuration name')
    parser.add_argument('--build-mode', default='Build', help='Build mode (Build or Rebuild)')
    args = parser.parse_args()

    build(args.exe, args.project, args.config, args.build_mode)
