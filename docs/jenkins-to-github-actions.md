# Converting a B&R Library from Jenkins to GitHub Actions

This document describes how to migrate a Loupe **B&R Automation Studio**
library repository from a Jenkins-based build (using
`buildPublishPipeline` from `jenkinsLibrary`) to **GitHub Actions** using
the reusable composite actions in
[`loupeteam/br-actions`](https://github.com/loupeteam/br-actions).

It's based on the migration of `loupeteam/StringExt` and is intended as
the template for all other Loupe public B&R library repos.

---

## 1. Background

### What the Jenkins pipeline did

A typical `Jenkinsfile` in a Loupe library repo looked like this:

```groovy
library "jenkinsLibrary@develop"

buildPublishPipeline(
    slackChannel:      "sandbox-github",
    asProjectRepo:     "stringext",
    asProjectVersion:  "main",
    asProjectPath:     "example/AsProject",
    skipLpmInstall:    true,
    packagesToPublish: ['src/Ar/stringext']
)
```

`buildPublishPipeline` (a shared library) handled:

1. Checkout
2. Resolving an AS install on the Jenkins agent
3. Building the example project for each configuration
4. Exporting the library into the LPM distribution layout
5. Publishing the result to the Loupe package registry
6. Slack notifications

### What replaces it

GitHub Actions, with three composite actions hosted in `loupeteam/br-actions`:

| Action | Purpose |
|--------|---------|
| `find-as6-build` | Locate `BR.AS.Build.exe` on the runner |
| `build-as-project` | Build a single AS6 project configuration |
| `export-as-library` | Export a built library into the LPM layout |
| `prepare-lpm-package` | Generate `package.json` (version + deps from `.lby`) |

Two workflows live in the consuming repo:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `build.yml` | manual | Sanity build for PRs / branches |
| `build-export-publish.yml` | tag `v*.*.*` or manual | Build → Export → Publish to GitHub Packages |

---

## 2. Prerequisites

- A self-hosted GitHub Actions runner with the label `AS6-runner` and:
  - Windows
  - Automation Studio 6 installed (default location
    `C:\BrAutomation\AS6` is auto-detected; otherwise set
    `BR_AS6_BUILD_PATH` as a runner env var)
  - **Python 3.x installed on the runner and on `PATH`** — the
    composite actions invoke `python` directly. We deliberately do
    *not* use `actions/setup-python` because on a self-hosted runner
    it re-downloads/extracts Python on every run (~30s+ overhead).
    Install Python once on the runner box (e.g.
    `winget install Python.Python.3.13`) and restart the runner
    service so the new `PATH` takes effect.
  - Node.js available — needed only by the publish workflow.
    `actions/setup-node` is still used because we need it to write
    the `.npmrc` for GitHub Packages auth, but if you'd rather use a
    runner-installed Node, see step 4.
- Repository permission to push to GitHub Packages (`packages: write`).
- The library example project upgraded to Automation Studio 6
  (the actions only support AS6).

### PR build approval (security)

The `Build` workflow runs on `pull_request`. To prevent untrusted
code from running on the self-hosted runner, set in the repo:

**Settings → Actions → General → Fork pull request workflows from
outside collaborators → Require approval for all outside
collaborators.**

With this on, PRs from forks/non-maintainers wait in a
*"Waiting for approval"* state until a maintainer clicks
**Approve and run**. Internal PRs (branches in the same repo) run
automatically.

> Do **not** switch the trigger to `pull_request_target` — that runs
> in the base branch's context with full secrets, defeating the
> approval gate.

---

## 3. Migration steps

### Step 1 — Upgrade the example project to AS6

The build pipeline only supports AS6. If your repo has an `AsProject/`
folder built with AS4, create a parallel `As6Project/` (or rename) and
upgrade it. For StringExt the layout is:

```
example/
  As6Project/
    AsProject.apj
    Logical/...
    Physical/
      Intel/   (5PC900_TS17_04)
      ARM/     (X20CP0410)
```

Pick configurations that exercise both x86 and ARM targets so the
exporter can produce SG4 and SG4_ARM artifacts.

### Step 2 — Add a `package.json` template to the library source

In the library source directory (e.g. `src/Ar/StringExt`) add a
template `package.json`:

```json
{
  "name": "@loupeteam/stringext",
  "version": "0.0.0",
  "description": "String manipulation functions for B&R Automation Studio (ANSIC library)",
  "homepage": "https://loupeteam.github.io/LoupeDocs/libraries/stringext.html",
  "author": "Loupe",
  "license": "MIT",
  "repository": {
    "type": "git",
    "url": "https://github.com/loupeteam/stringext"
  },
  "publishConfig": {
    "registry": "https://npm.pkg.github.com"
  },
  "lpm": { "type": "library" },
  "dependencies": {}
}
```

Notes:

- `version` is overwritten by the publish workflow.
- `dependencies` is overwritten by `prepare-lpm-package` from the
  `.lby` `<Dependencies>`. To pin a specific range for a Loupe dep,
  add it here (e.g. `"@loupeteam/something": ">=0.11.0"`); otherwise
  `*` is used.
- AS built-ins (`astime`, `AsBrStr`, `AsBrWStr`, `brsystem`, etc.) are
  filtered out automatically.

### Step 3 — Add the build workflow

Create `.github/workflows/build.yml`:

```yaml
name: Build

on:
  workflow_dispatch:
    inputs:
      ref:
        description: 'Branch, tag, or SHA to build'
        required: false
        default: ''
      build-mode:
        description: 'Build mode'
        required: false
        default: 'Build'
        type: choice
        options: [Build, Rebuild]
  # Build PRs. Outside contributors are gated by the repo-level
  # "Require approval" setting (see Prerequisites). Do NOT use
  # pull_request_target.
  pull_request:
    paths-ignore:
      - '**/*.md'
      - 'docs/**'
      - 'LICENSE'

jobs:
  build:
    runs-on: [AS6-runner]

    env:
      PROJECT: example/As6Project/AsProject.apj

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          ref: ${{ inputs.ref }}

      - name: Find AS6 build executable
        uses: loupeteam/br-actions/find-as6-build@v1
        id: find-as

      - name: Build Intel
        uses: loupeteam/br-actions/build-as-project@v1
        with:
          exe-path:   ${{ steps.find-as.outputs.exe-path }}
          project:    ${{ env.PROJECT }}
          config:     Intel
          # `|| 'Build'` makes pull_request runs (no inputs) work.
          build-mode: ${{ inputs.build-mode || 'Build' }}

      - name: Build ARM
        uses: loupeteam/br-actions/build-as-project@v1
        with:
          exe-path:   ${{ steps.find-as.outputs.exe-path }}
          project:    ${{ env.PROJECT }}
          config:     ARM
          build-mode: ${{ inputs.build-mode || 'Build' }}

      - name: Upload build diagnostics
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: BuildDiagnostics
          path: example/As6Project/Temp/BuildDiagnostics.log
          if-no-files-found: ignore
```

> Tip: only `PROJECT` is configured per-repo. The publish workflow
> (next step) derives the project directory and library name
> automatically.

### Step 4 — Add the build/export/publish workflow

Create `.github/workflows/build-export-publish.yml`:

```yaml
name: Build, Export, and Publish

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to publish (e.g. v0.16.0)'
        required: true

jobs:
  build-export-publish:
    runs-on: [AS6-runner]

    permissions:
      contents: read
      packages: write

    env:
      PROJECT:     example/As6Project/AsProject.apj
      LIBRARY_DIR: src/Ar/StringExt
      EXPORT_DIR:  export

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          registry-url: 'https://npm.pkg.github.com'
          scope: '@loupeteam'

      - name: Resolve version
        id: version
        shell: pwsh
        run: |
          if ($env:GITHUB_REF_TYPE -eq 'tag') {
            $ver = $env:GITHUB_REF_NAME -replace '^v', ''
          } else {
            $ver = "${{ inputs.version }}" -replace '^v', ''
          }
          if (-not $ver) { Write-Error "No version"; exit 1 }
          "version=$ver" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT

      - name: Find AS6 build executable
        uses: loupeteam/br-actions/find-as6-build@v1
        id: find-as

      # Derive the project directory and library name once, so per-repo
      # customisation is limited to PROJECT and LIBRARY_DIR. The library
      # name is the basename of the .lby file (typically `ANSIC` for C
      # libraries, `IEC` for ST libraries).
      - name: Resolve project paths
        id: project
        shell: pwsh
        run: |
          $apj = "${{ env.PROJECT }}"
          if (-not (Test-Path $apj)) { Write-Error "Project file not found: $apj"; exit 1 }
          $dir = Split-Path $apj -Parent
          "dir=$dir" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT

          $libDir = "${{ env.LIBRARY_DIR }}"
          $lby = Get-ChildItem -Path $libDir -Filter *.lby -File |
                   Select-Object -First 1
          if (-not $lby) { Write-Error "No .lby file found in: $libDir"; exit 1 }
          $libName = [System.IO.Path]::GetFileNameWithoutExtension($lby.Name)
          "library=$libName" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT

      - name: Build Intel
        uses: loupeteam/br-actions/build-as-project@v1
        with:
          exe-path: ${{ steps.find-as.outputs.exe-path }}
          project:  ${{ env.PROJECT }}
          config:   Intel

      - name: Build ARM
        uses: loupeteam/br-actions/build-as-project@v1
        with:
          exe-path: ${{ steps.find-as.outputs.exe-path }}
          project:  ${{ env.PROJECT }}
          config:   ARM

      - name: Export library
        uses: loupeteam/br-actions/export-as-library@v1
        with:
          project-dir: ${{ steps.project.outputs.dir }}
          library:     ${{ steps.project.outputs.library }}
          library-dir: ${{ env.LIBRARY_DIR }}
          configs:     Intel ARM
          output:      ${{ env.EXPORT_DIR }}
          as-install:  ${{ steps.find-as.outputs.install-path }}

      # Required: the export action writes to `<EXPORT_DIR>/<library>/V<lbyVersion>/`,
      # where `<lbyVersion>` comes from the .lby's Version attribute, not the
      # publish version we resolved above. Downstream steps need the actual path.
      - name: Locate exported version directory
        id: export-dir
        shell: pwsh
        run: |
          $path = Get-ChildItem "$env:EXPORT_DIR/${{ steps.project.outputs.library }}" -Directory |
                  Select-Object -First 1 -ExpandProperty FullName
          "path=$path" | Out-File -Append -Encoding utf8 -FilePath $env:GITHUB_OUTPUT

      - name: Generate package.json
        uses: loupeteam/br-actions/prepare-lpm-package@v1
        with:
          library-dir: ${{ env.LIBRARY_DIR }}
          output-dir:  ${{ steps.export-dir.outputs.path }}
          version:     ${{ steps.version.outputs.version }}

      # Without this, GitHub Packages shows "No readme data found" on each
      # version page (the package landing page falls back to the repo README,
      # but version pages only show what's inside the published tarball).
      - name: Copy README into package
        shell: pwsh
        run: |
          Copy-Item -Path README.md -Destination "${{ steps.export-dir.outputs.path }}/README.md" -Force

      - name: Publish to GitHub Packages
        shell: pwsh
        env:
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          Push-Location "${{ steps.export-dir.outputs.path }}"
          npm publish
          Pop-Location

      - name: Upload exported library artifact
        uses: actions/upload-artifact@v4
        with:
          name: ${{ steps.project.outputs.library }}-${{ steps.version.outputs.version }}
          path: ${{ env.EXPORT_DIR }}/${{ steps.project.outputs.library }}/
          if-no-files-found: error
```

### Step 5 — Adjust per-library values

For each library, update the `env:` block in both workflows:

| Variable | Example | Notes |
|----------|---------|-------|
| `PROJECT` | `example/As6Project/AsProject.apj` | Path to `.apj`. The publish workflow derives the project directory from this. |
| `LIBRARY_DIR` | `src/Ar/StringExt` | Source dir holding the `.lby` + `package.json`. The library name (`.lby` basename — usually `ANSIC` for C libraries, `IEC` for ST libraries) is auto-detected. |
| `EXPORT_DIR` | `export` | Output root |
| `Build Intel/ARM` step `config:` | `Intel` / `ARM` | Must match `Physical/<config>/` folder names |

> Note: Don't rename the `.lby` file. AS expects the standard names
> (`ANSIC.lby` for C libraries, `IEC.lby` for ST libraries) — that's
> the on-disk identifier used by the build, not the package name. The
> npm package name comes from `package.json`.

### Step 6 — Remove the Jenkinsfile

```bash
git rm Jenkinsfile
git commit -m "Remove Jenkinsfile - migrated to GitHub Actions"
```

### Step 7 — Land workflows on the default branch

Important: `workflow_dispatch` only appears in the **Actions** tab if
the workflow file exists on the **default branch** (typically `main`).
You can develop on a feature branch, but to be able to manually trigger
runs you must merge the workflow files to `main` (or cherry-pick them
there directly).

### Step 8 — First test run

Three ways to trigger the build workflow:

- **Open a PR** — the `pull_request` trigger runs the build
  automatically (or after maintainer approval for outside
  contributors). This is the normal day-to-day mode.
- **Manual dispatch** — Open **Actions** → **Build** →
  **Run workflow**, set `ref` to your feature branch if needed.
- **Push to default branch** — not enabled by default; add a
  `push:` trigger if you want it.

Verify both `Build Intel` and `Build ARM` succeed.

### Step 9 — First publish run

1. Open **Actions** → **Build, Export, and Publish** → **Run workflow**.
2. Enter a test version like `v0.0.1-test` and run.
3. Inspect the published package under
   <https://github.com/orgs/loupeteam/packages>.
4. Verify the exported layout (see below) and that `package.json`
   contains the expected `dependencies`.
5. Once happy, push a real tag: `git tag v0.16.0 && git push --tags`.

---

## 4. Reference

### Expected exported layout

```
{export-dir}/{Library}/V{Version}/
  Binary.lby                 # .lby with SubType=Binary, source files stripped
  *.fun / *.typ / *.var      # declaration files
  package.json               # generated from template + .lby deps
  SG3/  {Library}.h  lib{Library}.a
  SGC/  {Library}.h  lib{Library}.a
  SG4/  {Library}.h  lib{Library}.a  {Library}.br        # x86
    Arm/  lib{Library}.a  {Library}.br                   # ARM
```

### Pinning a `br-actions` version

The examples use `@v1`, which is a moving tag. To pin to a specific
release, replace `@v1` with `@<sha>` once that's appropriate. Don't
pin to `@main` in production.

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Workflow doesn't appear in **Actions** tab | File not on default branch | Merge/cherry-pick the workflow to `main` |
| `bash: command not found` | Step uses `shell: bash` on Windows runner | Use `shell: pwsh` |
| `python: command not found` | Python not installed on the runner / not on `PATH` | Install Python on the runner box (see Prerequisites) and restart the runner service |
| `setup-python` runs slowly every build (~30s) | Self-hosted runner has no persistent toolcache | Remove `actions/setup-python` and rely on runner-installed Python |
| Project file not found at `.../AsProject.apj` | Checkout pulled a branch that lacks `As6Project/` | Use the `ref` input or merge the AS6 project to `main` |
| `BR.AS.Build.exe` ... `Must specify valid information for parsing` | Empty value passed for `-buildMode` (e.g. `inputs.build-mode` is unset on `pull_request`) | Use `${{ inputs.build-mode || 'Build' }}`; fixed in `br-actions` ≥ `v1` |
| `BR.AS.Build.exe` exits 1 with only warnings | AS treats warnings as errors at exit code | Already tolerated — `build-as-project` only fails on real errors |
| Intel artifacts missing from `SG4/` | Both configs detected as `SG4_ARM` | Fixed in `br-actions` ≥ `v1` (ELF arch sniff) |
| `UnicodeEncodeError` on `→` | Windows runner stdout = cp1252 | Fixed in `br-actions` ≥ `v1` (forces UTF-8 stdout) |
| PR from a fork sits in *Waiting for approval* | Repo policy requires approval for outside collaborators | Maintainer clicks **Approve and run** — this is intentional |
| `npm publish` 409 / 403 | Re-publishing same version, or token lacks `packages: write` | Bump version, or add the permissions block |
| Version page shows "No readme data found" | `npm publish` only includes a README if one sits next to `package.json` in the published tarball; the exported version dir doesn't have one | Add a `Copy README into package` step before publish (already in the template) |
| `Get-ChildItem` returns nothing for `Temp/Objects/<config>/<cpu>/<library>/` during export | The library identifier passed to `export-as-library` doesn't match the `.lby` basename | Don't override the auto-detected library name; the `.lby` basename (`ANSIC` / `IEC`) is what AS uses for build artifacts |

### Useful links

- Composite actions: <https://github.com/loupeteam/br-actions>
- Reference Jenkins shared lib (predecessor):
  <https://github.com/br-automation-community/BnR-Jenkins-Helper-Library>
