#!/usr/bin/env python3
"""
Generate/update a package.json for an exported B&R library.

Reads a template package.json from the library source directory, sets the
version, and syncs `dependencies` from the library's .lby <Dependencies>
entries:

  - Known AS built-ins are skipped (e.g. astime, AsBrStr, AsBrWStr, brsystem...)
  - Remaining ObjectNames are mapped to {scope}/{lowercased-name}
  - Version ranges come from the template package.json's `dependencies` if
    specified there, otherwise the supplied default range is used.

The result is written to {output-dir}/package.json.
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict

# Ensure stdout/stderr can encode non-ASCII output on Windows runners
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

LIB_NS = '{http://br-automation.co.at/AS/Library}'

# Standard B&R Automation Studio libraries shipped with AR. These are NOT
# packaged via LPM — they're always available on the target — so we skip them
# when generating the dependency list. Loaded from br-libraries.txt alongside
# this script.
_BR_LIBS_FILE = os.path.join(os.path.dirname(__file__), 'br-libraries.txt')
with open(_BR_LIBS_FILE, encoding='utf-8') as _f:
    DEFAULT_AS_BUILTINS = {line.strip() for line in _f if line.strip()}


def _read_lby_dependencies(library_dir: str) -> list[str]:
    lbys = [f for f in os.listdir(library_dir) if f.lower().endswith('.lby')]
    if not lbys:
        raise SystemExit(f'ERROR: no .lby file found in {library_dir}')
    lby_path = os.path.join(library_dir, lbys[0])

    root = ET.parse(lby_path).getroot()
    deps = root.find(LIB_NS + 'Dependencies')
    if deps is None:
        return []

    out: list[str] = []
    for d in deps.findall(LIB_NS + 'Dependency'):
        name = d.get('ObjectName')
        if name:
            out.append(name)
    return out


def _load_template_package(library_dir: str) -> dict:
    pkg_path = os.path.join(library_dir, 'package.json')
    if not os.path.isfile(pkg_path):
        return {}
    with open(pkg_path, 'r', encoding='utf-8') as f:
        return json.load(f, object_pairs_hook=OrderedDict)


def _split_extra(value: str) -> set[str]:
    if not value:
        return set()
    parts = re.split(r'[\s,]+', value.strip())
    return {p for p in parts if p}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--library-dir', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--version', required=True)
    ap.add_argument('--scope', default='@loupeteam')
    ap.add_argument('--default-range', default='*')
    ap.add_argument('--extra-builtins', default='')
    args = ap.parse_args()

    library_dir = os.path.abspath(args.library_dir)
    output_dir  = os.path.abspath(args.output_dir)
    version     = args.version.lstrip('vV')
    scope       = args.scope.rstrip('/')

    if not os.path.isdir(library_dir):
        print(f'ERROR: library-dir does not exist: {library_dir}', file=sys.stderr)
        return 1
    if not os.path.isdir(output_dir):
        print(f'ERROR: output-dir does not exist: {output_dir}', file=sys.stderr)
        return 1

    builtins = set(DEFAULT_AS_BUILTINS) | _split_extra(args.extra_builtins)

    pkg = _load_template_package(library_dir)
    pkg['version'] = version

    template_deps = pkg.get('dependencies') or {}
    if not isinstance(template_deps, dict):
        template_deps = {}

    new_deps: 'OrderedDict[str, str]' = OrderedDict()
    for obj in _read_lby_dependencies(library_dir):
        if obj in builtins:
            print(f'  Skipping AS built-in dependency: {obj}')
            continue
        pkg_name = f'{scope}/{obj.lower()}'
        version_range = template_deps.get(pkg_name, args.default_range)
        new_deps[pkg_name] = version_range
        print(f'  Loupe dependency: {obj} -> {pkg_name}@{version_range}')

    pkg['dependencies'] = new_deps

    out_path = os.path.join(output_dir, 'package.json')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(pkg, f, indent=2)
        f.write('\n')

    print(f'\nWrote {out_path}')
    print(f'  name    : {pkg.get("name", "(none)")}')
    print(f'  version : {pkg.get("version")}')
    print(f'  deps    : {len(new_deps)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
