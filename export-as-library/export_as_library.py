#!/usr/bin/env python3
"""
Export a compiled B&R Automation Studio library.

Produces the standard Loupe/LPM distribution layout under output/{Library}/{Version}/:

    Binary.lby          — .lby with SubType=Binary, source objects stripped
    *.fun / *.typ / *.var — declaration files (non-source)
    SG3/
        {Library}.h
        lib{Library}.a   (if built for SG3)
    SGC/
        {Library}.h
        lib{Library}.a   (if built for SGC)
    SG4/
        {Library}.h
        {Library}.br     (Intel .br — if present)
        lib{Library}.a   (Intel .a)
        Arm/
            {Library}.br (ARM .br — if present)
            lib{Library}.a

The project must already be built (Temp/ and Binaries/ directories must exist).

References:
    https://github.com/br-automation-community/BnR-Jenkins-Helper-Library
    resources/scripts/ASProject.py  (ASLibrary.export)
    resources/scripts/InstalledAS.py
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

# Ensure stdout/stderr can encode non-ASCII output (e.g. arrows) on Windows runners
# where the default code page is cp1252.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ELF e_machine values used to detect CPU architecture from compiled object files
_ELF_MAGIC = b'\x7fELF'
_ELF_EM_386    = 0x0003  # IA-32 (SG4)
_ELF_EM_ARM    = 0x0028  # ARM 32-bit (SG4_ARM)
_ELF_EM_AARCH64 = 0x00B7  # ARM 64-bit (SG4_ARM)

# ---------------------------------------------------------------------------
# Source-file detection — mirrors ASLibrary.__isSourceFile in the B&R reference
# ---------------------------------------------------------------------------
_SOURCE_EXTENSIONS = frozenset({'.c', '.cpp', '.h', '.hpp', '.st', '.ab'})


def _is_source_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    if ext.lower() in _SOURCE_EXTENSIONS:
        return True
    if os.path.basename(filename).startswith('.clang'):
        return True
    return False


def _sniff_elf_arch(project_dir: str, config_name: str, cpu_name: str, library_name: str) -> str | None:
    """
    Detect SG4 vs SG4_ARM by reading the ELF e_machine field from a compiled .o
    file in Temp/Objects/{config}/{cpu}/{library}/.
    Returns 'SG4', 'SG4_ARM', or None if no suitable file is found.
    """
    obj_dir = os.path.join(project_dir, 'Temp', 'Objects', config_name, cpu_name, library_name)
    if not os.path.isdir(obj_dir):
        return None
    for fname in os.listdir(obj_dir):
        if not fname.endswith('.o'):
            continue
        try:
            with open(os.path.join(obj_dir, fname), 'rb') as f:
                header = f.read(20)
            if len(header) < 20 or header[:4] != _ELF_MAGIC:
                continue
            machine = struct.unpack_from('<H', header, 18)[0]
            if machine in (_ELF_EM_ARM, _ELF_EM_AARCH64):
                return 'SG4_ARM'
            return 'SG4'
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# AS project helpers
# ---------------------------------------------------------------------------

def _get_cpu_name(project_dir: str, config_name: str) -> str:
    """Read the CPU module name from Physical/{config}/Config.pkg."""
    config_pkg = os.path.join(project_dir, 'Physical', config_name, 'Config.pkg')
    NS = '{http://br-automation.co.at/AS/Configuration}'
    root = ET.parse(config_pkg).getroot()
    objects = root.find(NS + 'Objects')
    if objects is None:
        raise RuntimeError(f'No <Objects> element found in {config_pkg}')
    for obj in objects.findall(NS + 'Object'):
        if obj.get('Type') == 'Cpu':
            return obj.text
    raise RuntimeError(f'No CPU entry found in {config_pkg}')


def _get_cpu_architecture(
    project_dir: str,
    config_name: str,
    cpu_name: str,
    as_install: str = '',
    library_name: str = '',
) -> str:
    """
    Determine the CPU architecture from post-build artifacts.

    Returns one of: 'SG3', 'SGC', 'SG4' (IA32), 'SG4_ARM'.
    Mirrors ASConfiguration.cpuArchitecture() from the B&R reference scripts.
    """
    opt_file = os.path.join(
        project_dir, 'Temp', 'Objects', config_name, 'ConfigurationOptions.opt'
    )
    if not os.path.isfile(opt_file):
        raise RuntimeError(
            f'ConfigurationOptions.opt not found: {opt_file}\n'
            'Was the project built before running export?'
        )

    root = ET.parse(opt_file).getroot()
    target = root.get('Target', '')

    if target in ('SG3', 'SGC'):
        return target

    if target == 'SG4':
        # Check for ARM hardware descriptor generated during build
        ashwd = os.path.join(
            project_dir, 'Temp', 'Objects', config_name, cpu_name, 'ashwd.br.tmp.xml'
        )
        if not os.path.isfile(ashwd):
            return 'SG4'  # No ARM descriptor → IA32

        # Parse hardware config short name
        try:
            hw_root = ET.parse(ashwd).getroot()
            ns = {'hw': 'http://br-automation.com/AR/IO/HWD'}
            param = hw_root.find('.//hw:Hardware/hw:Parameter[@ID="HwcShortName"]', ns)
            if param is None:
                return 'SG4'
            hw_name = param.get('Value', '')
        except ET.ParseError:
            return 'SG4'

        if not hw_name or not as_install:
            # Without AS install path, sniff the ELF header of a compiled object file
            if library_name:
                elf_arch = _sniff_elf_arch(project_dir, config_name, cpu_name, library_name)
                if elf_arch is not None:
                    return elf_arch
            # Fall back: ashwd exists but architecture unconfirmed — assume ARM
            return 'SG4_ARM'

        # Confirm ARM by checking for the board config file in the AS installation
        try:
            cpu_pkg = os.path.join(
                project_dir, 'Physical', config_name, cpu_name, 'Cpu.pkg'
            )
            NS_CPU = '{http://br-automation.co.at/AS/Cpu}'
            cpu_root = ET.parse(cpu_pkg).getroot()
            ar_ver = (
                cpu_root.find(f'.//{NS_CPU}Configuration')
                        .find(f'{NS_CPU}AutomationRuntime')
                        .get('Version', '')
            )
            # e.g. "I4.93" → dir name "I40093" (AS convention)
            ar_dir = ar_ver.replace('.', '')
            ar_dir = ar_dir[:1] + '0' + ar_dir[1:]
            arm_board = os.path.join(
                as_install, 'System', ar_dir, 'SG4', 'ARM', f'@cf{hw_name}.br'
            )
            if os.path.isfile(arm_board):
                return 'SG4_ARM'
        except Exception:
            pass

        # ashwd exists but board file not confirmed — still treat as ARM
        return 'SG4_ARM'

    # Unknown target — warn and default to SG4
    print(
        f'WARNING: Unknown Target="{target}" in ConfigurationOptions.opt — defaulting to SG4',
        file=sys.stderr,
    )
    return 'SG4'


def _find_library_dir(project_dir: str, library_name: str) -> str | None:
    """Search Logical/ recursively for a directory containing {library_name}.lby."""
    logical = os.path.join(project_dir, 'Logical')
    name_lower = library_name.lower()
    for dirpath, _dirs, files in os.walk(logical):
        for f in files:
            if f.endswith('.lby') and os.path.splitext(f)[0].lower() == name_lower:
                return dirpath
    return None


def _read_library_meta(lib_dir: str) -> tuple[str, str, list[str]]:
    """
    Parse the .lby file.

    Returns (lby_path, version_string, list_of_file_entries).
    version_string is normalised to 'VX.YY.Z' format.
    """
    lby_files = [f for f in os.listdir(lib_dir) if f.endswith('.lby')]
    if not lby_files:
        raise RuntimeError(f'No .lby file found in {lib_dir}')
    lby_path = os.path.join(lib_dir, lby_files[0])
    root = ET.parse(lby_path).getroot()

    version = root.get('Version', 'V1.00.0')
    if not version.startswith('V'):
        version = 'V' + version
    parts = version.split('.')
    if len(parts) >= 2 and len(parts[1]) < 2:
        parts[1] = parts[1].zfill(2)
    version = '.'.join(parts)

    NS = '{http://br-automation.co.at/AS/Library}'
    files: list[str] = []
    for container_tag in (NS + 'Files', NS + 'Objects'):
        container = root.find(container_tag)
        if container is not None:
            for child in container:
                child_type = child.get('Type', 'File')
                if child_type == 'File' and child.text:
                    files.append(child.text)
            break

    return lby_path, version, files


def _create_binary_lby(lby_src: str, export_dir: str) -> None:
    """
    Write Binary.lby to export_dir.

    Copies the original .lby, sets SubType="Binary", and removes source-file
    entries from the Objects/Files list.
    """
    dst = os.path.join(export_dir, 'Binary.lby')
    shutil.copyfile(lby_src, dst)

    ET.register_namespace('', 'http://br-automation.co.at/AS/Library')
    tree = ET.parse(dst)
    root = tree.getroot()
    root.set('SubType', 'Binary')

    NS = '{http://br-automation.co.at/AS/Library}'
    for container_tag in (NS + 'Files', NS + 'Objects'):
        container = root.find(container_tag)
        if container is None:
            continue
        to_remove = [
            child for child in container
            if child.get('Type', 'File') != 'File' or _is_source_file(child.text or '')
        ]
        for child in to_remove:
            container.remove(child)
        break

    tree.write(dst, xml_declaration=True, encoding='utf-8')


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_library(
    project_dir: str,
    library_name: str,
    config_names: list[str],
    output_dir: str,
    library_dir: str = '',
    as_install: str = '',
) -> None:

    # Resolve library source directory
    lib_dir = library_dir.strip() if library_dir else ''
    if not lib_dir:
        lib_dir = _find_library_dir(project_dir, library_name)
        if lib_dir is None:
            print(
                f'ERROR: Library "{library_name}" not found under {project_dir}/Logical/',
                file=sys.stderr,
            )
            sys.exit(1)

    lby_path, version, file_entries = _read_library_meta(lib_dir)
    print(f'Exporting  : {library_name}')
    print(f'Version    : {version}')
    print(f'Source dir : {lib_dir}')
    print(f'Configs    : {", ".join(config_names)}')

    with tempfile.TemporaryDirectory() as tmpdir:
        export_subdir = os.path.join(tmpdir, version)
        os.makedirs(export_subdir)

        # 1. Copy non-source declaration files listed in the .lby
        for entry in file_entries:
            if _is_source_file(entry):
                continue
            src = os.path.join(lib_dir, entry)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(export_subdir, entry))

        # 2. Create Binary.lby
        _create_binary_lby(lby_path, export_subdir)

        # 3. Create architecture subdirectories
        for arch_dir in ('SG3', 'SGC', 'SG4'):
            os.makedirs(os.path.join(export_subdir, arch_dir))

        # 4. Copy Help folder (optional)
        help_chm = os.path.join(lib_dir, 'Help', f'Lib{library_name}.chm')
        if os.path.isfile(help_chm):
            os.makedirs(os.path.join(export_subdir, 'Help'))
            shutil.copyfile(help_chm, os.path.join(export_subdir, 'Help', f'Lib{library_name}.chm'))

        temp_dir = os.path.join(project_dir, 'Temp')

        for config_name in config_names:
            print(f'\nProcessing config: {config_name}')
            cpu_name = _get_cpu_name(project_dir, config_name)
            arch = _get_cpu_architecture(project_dir, config_name, cpu_name, as_install, library_name)
            print(f'  CPU      : {cpu_name}')
            print(f'  Arch     : {arch}')

            # 5. Copy header to all SG directories
            header_src = os.path.join(temp_dir, 'Includes', f'{library_name}.h')
            if os.path.isfile(header_src):
                for arch_dir in ('SG3', 'SGC', 'SG4'):
                    shutil.copyfile(
                        header_src,
                        os.path.join(export_subdir, arch_dir, f'{library_name}.h'),
                    )
            else:
                print(f'  WARNING: Header not found at {header_src}', file=sys.stderr)

            # 6. Determine target architecture subdirectory
            if arch == 'SG3':
                target_dir = os.path.join(export_subdir, 'SG3')
            elif arch == 'SGC':
                target_dir = os.path.join(export_subdir, 'SGC')
            elif arch == 'SG4_ARM':
                target_dir = os.path.join(export_subdir, 'SG4', 'Arm')
                os.makedirs(target_dir, exist_ok=True)
            else:  # SG4 IA32
                target_dir = os.path.join(export_subdir, 'SG4')

            # 7. Copy compiled .a archive
            a_src = os.path.join(
                temp_dir, 'Archives', config_name, cpu_name, f'lib{library_name}.a'
            )
            if os.path.isfile(a_src):
                shutil.copyfile(a_src, os.path.join(target_dir, f'lib{library_name}.a'))
                print(f'  Copied   : lib{library_name}.a → {os.path.relpath(target_dir, export_subdir)}/')
            else:
                print(f'  WARNING: Archive not found at {a_src}', file=sys.stderr)

            # 8. Copy .br binary (optional — not always present)
            br_src = os.path.join(
                project_dir, 'Binaries', config_name, cpu_name, f'{library_name}.br'
            )
            if os.path.isfile(br_src):
                shutil.copyfile(br_src, os.path.join(target_dir, f'{library_name}.br'))
                print(f'  Copied   : {library_name}.br → {os.path.relpath(target_dir, export_subdir)}/')

        # 9. Move assembled tree to final output location
        final_dest = os.path.join(output_dir, library_name, version)
        if os.path.exists(final_dest):
            shutil.rmtree(final_dest)
        os.makedirs(os.path.dirname(final_dest), exist_ok=True)
        shutil.copytree(export_subdir, final_dest)

    print(f'\nExport complete: {final_dest}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export a compiled AS library')
    parser.add_argument('--project-dir',  required=True,  help='AS project directory')
    parser.add_argument('--library',      required=True,  help='Library name (e.g. StringExt)')
    parser.add_argument('--library-dir',  default='',     help='Library source directory (optional)')
    parser.add_argument('--configs',      required=True,  help='Space-separated config names')
    parser.add_argument('--output',       required=True,  help='Output directory')
    parser.add_argument('--as-install',   default='',     help='AS6 install path (for ARM detection)')
    args = parser.parse_args()

    export_library(
        project_dir=args.project_dir,
        library_name=args.library,
        config_names=args.configs.split(),
        output_dir=args.output,
        library_dir=args.library_dir,
        as_install=args.as_install,
    )
