# bnr-build-actions

Reusable GitHub Actions composite actions for building and exporting
**B&R Automation Studio 6** libraries on self-hosted Windows runners.

## Actions

| Action | Description |
|--------|-------------|
| `find-as6-build` | Locates `BR.AS.Build.exe` for AS6 — handles runners with multiple AS versions installed |
| `build-as-project` | Builds a single AS6 project configuration via `BR.AS.Build.exe` |
| `export-as-library` | Exports a compiled library into the standard Loupe/LPM distribution layout |
| `prepare-lpm-package` | Generates a `package.json` for an exported library — sets the version and syncs `dependencies` from the `.lby` |
| `update-dtm-catalog` | Registers a project's FDT/DTM devices into the AS6 DTM catalog so headless builds resolve EtherCAT / FDT-DTM hardware (fixes build error 4836) |

## Requirements

- Windows runner with Automation Studio 6 installed
- Python 3.x available on the runner PATH (or via `actions/setup-python` in the calling workflow)

## Usage

```yaml
- name: Set up Python
  uses: actions/setup-python@v5
  with:
    python-version: '3.x'

- name: Find AS6 build executable
  uses: loupeteam/bnr-build-actions/find-as6-build@v1
  id: find-as

- name: Build Intel
  uses: loupeteam/bnr-build-actions/build-as-project@v1
  with:
    exe-path: ${{ steps.find-as.outputs.exe-path }}
    project:  example/AsProject/AsProject.apj
    config:   Intel

- name: Export library
  uses: loupeteam/bnr-build-actions/export-as-library@v1
  with:
    project-dir: example/AsProject
    library:     MyLib
    library-dir: src/Ar/MyLib
    configs:     Intel ARM
    output:      export
    as-install:  ${{ steps.find-as.outputs.install-path }}
```

For a project that includes third-party **FDT/DTM** devices (e.g. an EtherCAT
slave via the generic-slave DTM), refresh the DTM catalog after locating AS and
before building — otherwise the headless build fails with error `4836`:

```yaml
- name: Find AS6 build executable
  uses: loupeteam/bnr-build-actions/find-as6-build@v1
  id: find-as

- name: Update DTM catalog
  uses: loupeteam/bnr-build-actions/update-dtm-catalog@v1
  with:
    install-path: ${{ steps.find-as.outputs.install-path }}

- name: Build
  uses: loupeteam/bnr-build-actions/build-as-project@v1
  with:
    exe-path: ${{ steps.find-as.outputs.exe-path }}
    project:  AsProject/AsProject.apj
    config:   Config1
```

## Inputs & Outputs

### `find-as6-build`

**Outputs**

| Name | Description |
|------|-------------|
| `exe-path` | Full path to `BR.AS.Build.exe` |
| `install-path` | AS6 installation directory (parent of `Bin-en/`) |

Override discovery by setting `BR_AS6_BUILD_PATH` as a runner environment variable.

### `build-as-project`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `exe-path` | yes | — | Path to `BR.AS.Build.exe` |
| `project` | yes | — | Path to `.apj` file |
| `config` | yes | — | Configuration name (e.g. `Intel`) |
| `build-mode` | no | `Build` | `Build` or `Rebuild` |

### `export-as-library`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project-dir` | yes | — | AS project directory |
| `library` | yes | — | Library name |
| `library-dir` | no | *(auto-search)* | Library source directory |
| `configs` | yes | — | Space-separated config names |
| `output` | yes | — | Output root directory |
| `as-install` | no | — | AS6 install path (for ARM detection) |

### `update-dtm-catalog`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `install-path` | yes | — | AS6 install directory (`install-path` output of `find-as6-build`) |
| `bin-subdir` | no | `Bin-en` | Subfolder of `install-path` holding `BR.AS.Hardware.CLI.dll` |
| `import-files` | no | *(none)* | Device files (ESI / `.dtm`) to import before the refresh — one per line or comma-separated |
| `list-devices` | no | `true` | Print `Get-ThirdPartyDevices` for diagnostics |

Runs under 32-bit Windows PowerShell (the AS libraries it uses are 32-bit).
Only needed for projects with third-party FDT/DTM devices (e.g. an EtherCAT
slave); pure-library and B&R-only-hardware projects don't need it.

## Export layout

```
output/
  {Library}/
    {Version}/
      Binary.lby
      *.fun / *.typ / *.var
      SG3/  {Library}.h  lib{Library}.a
      SGC/  {Library}.h  lib{Library}.a
      SG4/  {Library}.h  lib{Library}.a  {Library}.br
        Arm/  {Library}.h  lib{Library}.a  {Library}.br
```

## License

MIT
