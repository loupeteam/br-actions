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


def find_ruc_package(project_dir: str, config: str):
    """Locate the RUC package a simulation build produced.

    The CPU folder under Binaries/<config>/ depends on the target hardware, so it
    is globbed rather than named. Returns the path, or None if there is not
    exactly one.
    """
    import glob
    import os
    pattern = os.path.join(project_dir, 'Binaries', config, '*', 'RUCPackage', 'RUCPackage.zip')
    matches = sorted(glob.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(f'::warning::No RUC package found under {pattern}', flush=True)
    else:
        print(f'::warning::{len(matches)} RUC packages matched {pattern}; expected one', flush=True)
    return None


def build(exe_path: str, project_apj: str, config: str, build_mode: str = 'Build',
          simulation: bool = False) -> None:
    # BR.AS.Build.exe requires an absolute path with Windows separators.
    # The runner's CWD is the workspace root, so relative paths (e.g. "example/As6Project/AsProject.apj")
    # resolve correctly via os.path.abspath, and os.path.normpath converts '/' to '\'.
    import os
    exe_path    = os.path.normpath(os.path.abspath(exe_path))
    project_apj = os.path.normpath(os.path.abspath(project_apj))
    project_dir = os.path.dirname(project_apj)

    # Diagnostics — print everything before invoking the subprocess
    print(f'CWD            : {os.getcwd()}', flush=True)
    print(f'exe_path       : {exe_path}   (exists={os.path.isfile(exe_path)})', flush=True)
    print(f'project_apj    : {project_apj}   (exists={os.path.isfile(project_apj)})', flush=True)
    print(f'project_dir    : {project_dir}   (isdir={os.path.isdir(project_dir)})', flush=True)
    if not os.path.isfile(project_apj):
        print(f'::error::Project file not found: {project_apj}', file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(project_dir):
        print(f'::error::Project directory not found: {project_dir}', file=sys.stderr)
        sys.exit(1)

    cmd = [exe_path, project_apj, '-c', config, '-buildMode', build_mode, '-all']
    if simulation:
        # Produces the ARsim binaries and packs the RUC package that
        # OfflineCommissioning (see the start-arsim action) installs.
        cmd += ['-simulation', '-buildRUCPackage']
    print(f'Running: {" ".join(cmd)}', flush=True)

    # Run from the project directory — BR.AS.Build.exe resolves relative paths
    # (e.g. Temp/, Binaries/) relative to its CWD, not the .apj location.
    # Merge stderr into stdout so both pipes are drained together — reading them
    # separately risks deadlock if the stderr buffer fills while we block on stdout.
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=project_dir
    )

    output_lines = []
    for line in process.stdout:
        print(line, end='', flush=True)
        output_lines.append(line)

    process.wait()

    combined = ''.join(output_lines)

    # If no build summary line is present, BR.AS.Build.exe printed usage/help and
    # didn't build at all (bad exe path, bad project path, or unsupported argument).
    if not re.search(r'\b\d+ error\(s\)', combined):
        print(
            '::error::BR.AS.Build.exe produced no build summary — '
            'check the exe path, project path, and config name',
            file=sys.stderr,
        )
        sys.exit(1)

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

    if simulation:
        ruc = find_ruc_package(project_dir, config)
        if ruc:
            print(f'RUC package: {ruc}', flush=True)
            github_output = os.environ.get('GITHUB_OUTPUT')
            if github_output:
                with open(github_output, 'a', encoding='utf-8') as handle:
                    handle.write(f'ruc-package={ruc}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build an AS6 project configuration')
    parser.add_argument('--exe',        required=True,  help='Path to BR.AS.Build.exe')
    parser.add_argument('--project',    required=True,  help='Path to the .apj file')
    parser.add_argument('--config',     required=True,  help='Configuration name')
    parser.add_argument('--build-mode', default='Build', help='Build mode (Build or Rebuild)')
    parser.add_argument('--simulation', default='',
                        help='Build for ARsim and pack a RUC package (true/false)')
    args = parser.parse_args()

    # Treat empty/whitespace as unset (e.g. when a workflow forwards an
    # unset workflow_dispatch input via ${{ inputs.build-mode }}).
    build_mode = (args.build_mode or '').strip() or 'Build'
    simulation = str(args.simulation).strip().lower() in ('true', '1', 'yes')
    build(args.exe, args.project, args.config, build_mode, simulation)
