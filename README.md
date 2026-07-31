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
| `start-arsim` | Installs a RUC package into an ARsim instance, starts it, and waits until the target is ready |
| `stop-arsim` | Stops ARsim instances and verifies they are actually gone |

## Requirements

- Windows runner with Automation Studio 6 installed
- Python 3.x available on the runner PATH (or via `actions/setup-python` in the calling workflow)
- PVI installed, for `start-arsim` / `stop-arsim` (they drive `PVITransfer.exe`)

### Choosing a runner

Which runner a repository can use depends on whether it is public or private:

```yaml
# Public repository — hosted, ephemeral Automation Studio image
runs-on: [AS6-runner]

# Private repository — the self-hosted pool, which is restricted to private repos
runs-on:
  group: private
  labels: [AS6]
```

Because `find-as6-build` discovers Automation Studio at runtime, the same workflow
shape works on either. `start-arsim` discovers `PVITransfer.exe` the same way, for the
same reason: the install root differs between a workstation-style install and a runner
image, and neither is more correct than the other.

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

## Running a project on ARsim

Build with `simulation: true` to produce a RUC package, then hand it to `start-arsim`.
A complete, copy-pasteable workflow is in
[`examples/arsim-integration.yml`](examples/arsim-integration.yml); the short version:

```yaml
- name: Build for simulation
  id: build
  uses: loupeteam/br-actions/build-as-project@v1
  with:
    exe-path:   ${{ steps.find-as.outputs.exe-path }}
    project:    AsProject/AsProject.apj
    config:     Intel
    simulation: 'true'

- name: Start ARsim
  id: arsim
  uses: loupeteam/br-actions/start-arsim@v1
  with:
    ruc-package: ${{ steps.build.outputs.ruc-package }}
    arsim-dir:   C:\arsim

- name: Run tests against the simulator
  run: <your test client>

# Last, and always: a simulator left running holds the target's ports and the
# next run fails while deploying.
- name: Stop ARsim
  if: always()
  uses: loupeteam/br-actions/stop-arsim@v1
  with:
    arsim-dir: C:\arsim
```

`ruc-package` also accepts a wildcard, for callers that build some other way — the CPU
folder under `Binaries/<config>/` depends on the target hardware:

```yaml
    ruc-package: AsProject/Binaries/Intel/*/RUCPackage/RUCPackage.zip
```

### Choosing a readiness check

`start-arsim` waits for the target to answer before returning, and the two available
signals answer different questions:

| `readiness` | What it proves | When it fails you |
|---|---|---|
| `pvi` | PVITransfer's `PLCStatus` reports RUN. The only signal that distinguishes a target that booted into **SERVICE** (user tasks not running) from one that is genuinely running. | The ANSL port must be reachable. When the runner is a Windows *service*, a first-run firewall prompt has nobody to answer it, so the rule is silently absent and every query times out. |
| `opcua` | A TCP connection to the OPC UA server port is accepted — usually what a test client actually needs. | Requires the OPC UA server to be enabled in the configuration. A target sitting in SERVICE can still accept connections, so this cannot detect that case. |
| `any` *(default)* | Whichever answers first; the winner is reported in the `ready-via` output. | — |
| `none` | Nothing. Starts the loader and returns. | — |

On timeout, the action prints diagnostics — process liveness and port reachability —
and names the most likely cause, because a loader that died, an application that
faulted, and a firewall dropping the port are indistinguishable from the outside.

### Notes that will save you a cycle

- **Keep `arsim-dir` short, and outside the workspace.** Automation Studio tooling is
  sensitive to long paths. Installed somewhere deep, the runtime starts and then exits
  before reaching RUN, with *nothing in the logbook* — which reads like an application
  crash rather than a path problem. A stable directory also means the runner registers
  the loader's firewall exception once instead of prompting on every run.
- **Make sure the RUC package is fresh.** A build that does not regenerate
  `RUCPackage.zip` leaves the previous one in place, and the simulator then runs an old
  binary while everything reports success. If tests fail against code that is plainly
  correct, check the package's timestamp before anything else.
- **Cleanup needs `if: always()`.** Composite actions cannot register a post-job hook,
  so `stop-arsim` is an ordinary step and only runs if the job reaches it — which covers
  failure and ordinary cancellation, but not a hard-killed job or a runner restart.
  `start-arsim` clears its own directory first, so a leak is recovered on the next run.
  To close the gap entirely, give the runner a job-completed hook
  (`ACTIONS_RUNNER_HOOK_JOB_COMPLETED`), which it runs after every job regardless of
  outcome.
- **A machine can run several simulators**, each bound to its own address. Both actions
  are therefore scoped by directory: `start-arsim` only replaces the instance in the
  directory it is about to overwrite, and `stop-arsim` takes an `arsim-dir` to limit
  what it kills. Omitting `arsim-dir` on `stop-arsim` stops **every** ARsim on the
  machine — right for a dedicated runner, destructive anywhere else.

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
| `simulation` | no | `false` | Add `-simulation -buildRUCPackage` for an ARsim build |

**Outputs**

| Name | Description |
|------|-------------|
| `ruc-package` | Path to the RUC package a simulation build produced. Empty unless `simulation` is true and exactly one package was found. |

### `export-as-library`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project-dir` | yes | — | AS project directory |
| `library` | yes | — | Library name |
| `library-dir` | no | *(auto-search)* | Library source directory |
| `configs` | yes | — | Space-separated config names |
| `output` | yes | — | Output root directory |
| `as-install` | no | — | AS6 install path (for ARM detection) |

### `start-arsim`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ruc-package` | yes | — | Path to `RUCPackage.zip`; may contain a wildcard, but must match exactly one file |
| `arsim-dir` | no | `C:\arsim` | Where to install the simulator — keep it short and outside the workspace |
| `readiness` | no | `any` | `opcua`, `pvi`, `any`, or `none` |
| `opcua-port` | no | `4840` | Port used by the `opcua` and `any` checks |
| `host` | no | `127.0.0.1` | Target address |
| `timeout` | no | `240` | Seconds to wait for readiness |
| `create-timeout` | no | `180` | Seconds allowed for `OfflineCommissioning` |
| `stop-existing` | no | `true` | Stop an instance already running in `arsim-dir` |
| `pvi-transfer` | no | *(auto)* | Path to `PVITransfer.exe` |

**Outputs**

| Name | Description |
|------|-------------|
| `arsim-dir` | Absolute path to the installation directory |
| `pid` | Process id of the launched loader |
| `ready-via` | Which check reported ready: `opcua`, `pvi`, or `none` |
| `mode` | Last AR operating mode PVI reported, when it answered at all |

Override `PVITransfer.exe` discovery by setting `BR_PVI_TRANSFER_PATH` as a runner
environment variable.

### `stop-arsim`

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `arsim-dir` | no | *(all)* | Only stop instances installed under this directory |
| `fail-on-survivor` | no | `true` | Fail the step if a process survives |
| `verify-timeout` | no | `10` | Seconds to wait for the processes to disappear |

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

## Testing the actions

`.github/workflows/test.yml` tests this repository in three layers, ordered by how
much they need to run:

| Layer | Runs on | Needs | When |
|---|---|---|---|
| **Logic** | `ubuntu-latest` + `windows-latest` | nothing | every push and PR |
| **Action wiring** | `windows-latest` (hosted) | nothing | every push and PR |
| **Integration** | `AS6-runner` | Automation Studio, PVI, a project | manual only |

**Logic** runs `tests/test_arsim.py` — plain `unittest`, no dependencies, a few
seconds. It covers the parts that decide things: `PLCStatus` log parsing, installation
directory scoping, RUC package resolution, and the input coercion that lets a workflow
forward an unset input. Run it locally with:

```bash
python -m unittest discover -s tests -v
```

**Action wiring** invokes the composite actions themselves on a hosted runner, proving
`action.yml` → script wiring works without Automation Studio or PVI. It asserts two
things a reader might not expect to be worth testing:

- `stop-arsim` with nothing running must **succeed**. It is used with `if: always()`,
  so a non-zero exit for "nothing to do" would redden every otherwise-clean run.
- `start-arsim` without PVI must fail with the message that *says* PVI is missing.
  Asserting on the failure is the only way to know the diagnostic reaches the user
  rather than something obscure from further in.

**Integration** is the only layer that starts a real simulator, and it needs a project
to build. There is no example project in this repository, so it takes the path as a
dispatch input:

```bash
gh workflow run test.yml -f project=AsProject/AsProject.apj -f config=Intel
```

It runs the full chain — `find-as6-build` → `build-as-project` (with `simulation`) →
`start-arsim` → `stop-arsim` — and fails if `start-arsim` reports ready without naming
which check answered. It runs on `AS6-runner`, since this repository is public and the
self-hosted pool is restricted to private repositories.

Note that the Automation Studio image is built to *build* projects; whether it also
carries PVI, which `start-arsim` needs, is worth confirming on the first run. If it does
not, `start-arsim` fails with a message naming the roots it searched, and the options
are to add PVI to the image or to run this layer from a private repository against the
self-hosted pool.

## License

MIT
