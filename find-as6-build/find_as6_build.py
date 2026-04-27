#!/usr/bin/env python3
"""
Locate BR.AS.Build.exe for Automation Studio 6.

Search order:
  1. BR_AS6_BUILD_PATH environment variable (explicit runner override)
  2. Windows registry  HKLM\\SOFTWARE\\WOW6432Node\\BR_AS_* (version 6.x entries)
  3. Common install paths

Prints two lines to stdout on success:
  Line 1: Full path to BR.AS.Build.exe
  Line 2: AS6 installation directory (parent of Bin-en/)

Exits with code 1 if not found.
"""
import os
import sys


def _find_in_registry():
    try:
        import winreg
    except ImportError:
        return None, None  # Non-Windows or winreg unavailable

    try:
        root_key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\WOW6432Node',
            0,
            winreg.KEY_READ,
        )
    except OSError:
        return None, None

    result_exe = None
    result_install = None
    i = 0
    while True:
        try:
            subkey_name = winreg.EnumKey(root_key, i)
        except OSError:
            break  # No more keys
        i += 1

        if not subkey_name.startswith('BR_AS_'):
            continue

        try:
            # Read version from ControlStudio sub-key
            ctrl = winreg.OpenKey(root_key, subkey_name + r'\ControlStudio')
            version, _ = winreg.QueryValueEx(ctrl, 'ProgrVersion')
            winreg.CloseKey(ctrl)
        except OSError:
            continue

        if not version.startswith('6.'):
            continue

        try:
            subkey = winreg.OpenKey(root_key, subkey_name)
            # BuRAutStudioPath holds the version-specific install dir (e.g. C:\BrAutomation\AS6)
            install_path, _ = winreg.QueryValueEx(subkey, 'BuRAutStudioPath')
            winreg.CloseKey(subkey)
        except OSError:
            continue

        exe = os.path.join(install_path, 'Bin-en', 'BR.AS.Build.exe')
        if os.path.isfile(exe):
            result_exe = exe
            result_install = install_path
            break  # Take the first valid AS6 installation found

    winreg.CloseKey(root_key)
    return result_exe, result_install


def find_as6():
    """Return (exe_path, install_path) for AS6, or (None, None) if not found."""

    # 1. Environment variable override
    env_path = os.environ.get('BR_AS6_BUILD_PATH', '').strip()
    if env_path and os.path.isfile(env_path):
        install = os.path.dirname(os.path.dirname(env_path))  # strip \Bin-en\BR.AS.Build.exe
        return env_path, install

    # 2. Windows registry
    exe, install = _find_in_registry()
    if exe:
        return exe, install

    # 3. Common fallback paths
    fallbacks = [
        r'C:\BrAutomation\AS6',
        r'C:\Program Files (x86)\BRAutomation\AS6',
    ]
    for install in fallbacks:
        exe = os.path.join(install, 'Bin-en', 'BR.AS.Build.exe')
        if os.path.isfile(exe):
            return exe, install

    return None, None


if __name__ == '__main__':
    exe, install = find_as6()
    if exe is None:
        print('ERROR: BR.AS.Build.exe for Automation Studio 6 was not found.', file=sys.stderr)
        print('Set the BR_AS6_BUILD_PATH environment variable on the runner to the full', file=sys.stderr)
        print('path of BR.AS.Build.exe (e.g. C:\\BrAutomation\\AS6\\Bin-en\\BR.AS.Build.exe).', file=sys.stderr)
        sys.exit(1)
    print(exe)
    print(install)
