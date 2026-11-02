# bnr-build-actions

Reusable GitHub Actions composite actions for building and exporting
**B&R Automation Studio 6** libraries on self-hosted Windows runners.

## Reusable workflows

Most repos only need a short file that calls one of these. Which one do I use?

| I want to | Workflow |
|-----------|----------|
| Build a project on PRs or on demand | `as-build.yml` |
| Build, export, and publish a binary library | `as-library-publish.yml` |
| Build and publish a source program package | `as-program-publish.yml` |
| Do something these can't, mid-job | The [composite actions](#actions) directly, in your own workflow |

### Calling them

Build:

```yaml
name: Build

on:
  workflow_dispatch:
    inputs:
      build_mode:
        description: 'Build mode'
        required: false
        default: 'Build'
        type: choice
        options: [Build, Rebuild]
  pull_request:
    paths-ignore:
      - '**/*.md'
      - 'docs/**'
      - 'LICENSE'

jobs:
  build:
    permissions:
      contents: read
    uses: loupeteam/br-actions/.github/workflows/as-build.yml@v1
    with:
      project:    example/AsProject/AsProject.apj
      build-mode: ${{ inputs.build_mode }}
```

Library publish. `as-program-publish.yml` is called the same way, with `package-dir` in place of `library` and `library-dir`:

```yaml
name: Build, Export, and Publish

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to publish (e.g. v1.0.0)'
        required: true

jobs:
  build-export-publish:
    permissions:
      contents: read
      packages: write
    uses: loupeteam/br-actions/.github/workflows/as-library-publish.yml@v1
    with:
      project:     example/AsProject/AsProject.apj
      library:     MyLib
      library-dir: src/Ar/MyLib
      version:     ${{ inputs.version }}
```

The publish workflows need a tag or a `version`, and publish under the `@loupeteam` scope.

### Inputs

| Input | Workflows | Default | Description |
|-------|-----------|---------|-------------|
| `project` | all | required | Path to the `.apj` file |
| `configs` | all | `Intel ARM` | Space-separated configurations. Supported: `Intel`, `ARM`, `ARsim` |
| `runner` | all | `AS6-runner` | Runner label with Automation Studio 6 installed. Set this when calling from outside Loupe |
| `pre-build-script` | all | — | PowerShell script in your repo, run after checkout and before the build |
| `post-build-script` | all | — | PowerShell script in your repo, run after the build in the same workspace |
| `build-mode` | build | `Build` | `Build` or `Rebuild` |
| `ref` | build | triggering ref | Branch, tag, or SHA to check out |
| `upload-build-output` | build | `false` | Upload the project `Binaries` folder as the `BuildOutput` artifact |
| `library` | library | required | Library name |
| `library-dir` | library | required | Library source directory |
| `copy-readme` | library | `false` | Copy the repo `README.md` into the package |
| `package-dir` | program | required | Program package directory |
| `artifact-prefix` | program | `package` | Name prefix of the package artifact |
| `version` | publish | pushed tag | Version to publish |

### Adding to a workflow

| Need | How |
|------|-----|
| A separate step before or after the build, such as a check or a notification | Add a job to your calling workflow and order it with `needs:` |
| Change files the build compiles | `pre-build-script`. A separate job has its own workspace, so its edits never reach the build |
| Work on the build output | `post-build-script`, or `upload-build-output` and download the artifact in a later job |
| Anything else mid-job | Your own workflow, using the composite actions |

### Pinning

Callers use `@v1`, so fixes reach them with each release. Breaking changes go to a new major version. To review each update yourself, pin the commit SHA with the tag in a comment.

The workflows pin every action they use by SHA, including the actions in this repo. A change to a composite action therefore takes two releases: release the action, then update the pins in the workflows and release again.

## Actions

| Action | Description |
|--------|-------------|
| `find-as6-build` | Locates `BR.AS.Build.exe` for AS6 — handles runners with multiple AS versions installed |
| `build-as-project` | Builds a single AS6 project configuration via `BR.AS.Build.exe` |
| `export-as-library` | Exports a compiled library into the standard Loupe/LPM distribution layout |
| `prepare-lpm-package` | Generates a `package.json` for an exported library — sets the version and syncs `dependencies` from the `.lby` |

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
