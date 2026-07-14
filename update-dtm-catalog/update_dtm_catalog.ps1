<#
.SYNOPSIS
  Register/refresh a project's FDT/DTM devices in the Automation Studio 6 DTM
  catalog via B&R's BR.AS.Hardware.CLI.

.DESCRIPTION
  Invoked by the update-dtm-catalog composite action. This script must run under
  32-bit Windows PowerShell (the AS libraries loaded by BR.AS.Hardware.CLI are
  32-bit); the action arranges that. All paths arrive as real process arguments
  rather than being embedded in a generated script, so no manual quoting or
  file-encoding handling is required here.

.NOTES
  Update-DtmCatalog only refreshes the catalog from DTMs already installed on the
  runner. It will not add a device whose base DTM component is missing, and it
  skips devices already present as duplicates. Registering a project's specific
  device therefore usually needs Import-DtmDevice (see -ImportFiles), and a
  successful run does NOT by itself guarantee a given device is registered — use
  -ExpectDevices to assert that and fail loudly when it is not.
#>
param(
    # AS6 Bin directory that contains BR.AS.Hardware.CLI.dll (e.g. <install>\Bin-en).
    [Parameter(Mandatory = $true)]
    [string]$BinPath,

    # Device description files (ESI / .dtm) to import before refreshing the catalog.
    # Must be absolute paths (the action resolves them against the workspace).
    [string[]]$ImportFiles = @(),

    # 'true' to print the registered third-party device catalog for diagnostics.
    [string]$ListDevices = 'true',

    # Device names that must be present after the update; the run fails if any are
    # missing. Matched case-insensitively as substrings of the device Name.
    [string[]]$ExpectDevices = @()
)

$ErrorActionPreference = 'Stop'

try {
    if (-not (Test-Path -LiteralPath $BinPath)) {
        throw "AS Bin directory not found: '$BinPath'."
    }
    Set-Location -LiteralPath $BinPath

    $dll = Join-Path $BinPath 'BR.AS.Hardware.CLI.dll'
    if (-not (Test-Path -LiteralPath $dll)) {
        throw "BR.AS.Hardware.CLI.dll not found at '$dll'."
    }
    Import-Module $dll

    foreach ($f in $ImportFiles) {
        if (-not (Test-Path -LiteralPath $f)) {
            throw "Import file not found: '$f'."
        }
        Write-Host "== Import-DtmDevice: $f =="
        Import-DtmDevice -FilePaths $f -Force -Verbose
    }

    Write-Host '== Update-DtmCatalog =='
    Update-DtmCatalog -Verbose

    # Read the registered catalog once. A failure here is only fatal when the
    # caller asked us to verify specific devices; otherwise the listing is
    # best-effort diagnostics and must not fail the build.
    $devices = $null
    try {
        $devices = Get-ThirdPartyDevices
    } catch {
        if ($ExpectDevices.Count) { throw }
        Write-Warning "Could not read third-party device catalog: $_"
    }

    if ($ListDevices -eq 'true' -and $devices) {
        Write-Host '== Registered third-party devices =='
        $devices | Sort-Object Vendor, Name | Format-Table Name, Version, Vendor -AutoSize | Out-Host
    }

    if ($ExpectDevices.Count) {
        $missing = foreach ($e in $ExpectDevices) {
            $hit = $devices | Where-Object {
                $_.Name -and $_.Name.IndexOf($e, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            }
            if (-not $hit) { $e }
        }
        if ($missing) {
            throw ("Expected DTM device(s) still not registered after update: " +
                ($missing -join ', ') + ". Provide the device description file via " +
                "'import-files', or install the base DTM component on the runner " +
                "(Update-DtmCatalog only refreshes already-installed DTMs).")
        }
    }
}
catch {
    Write-Error $_
    exit 1
}

exit 0
