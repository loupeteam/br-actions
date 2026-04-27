#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Create a versioned release and move the floating major tag (e.g. v1).

.EXAMPLE
    .\tag-release.ps1 v1.0.2 "Fix build log streaming and path resolution"
#>
param(
    [Parameter(Mandatory)]
    [string]$Version,       # e.g. v1.0.2

    [string]$Notes = ''    # Release notes / short description (optional)
)

# Derive the major floating tag from the version (v1.0.2 -> v1)
if ($Version -notmatch '^v(\d+)') {
    Write-Error "Version must start with 'v' followed by a number (e.g. v1.0.2)"
    exit 1
}
$majorTag = "v$($Matches[1])"

# Ensure we're on main and up to date
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne 'main') {
    Write-Error "Must be on main branch (currently on '$branch')"
    exit 1
}
git pull origin main --ff-only
if ($LASTEXITCODE -ne 0) { exit 1 }

$commit = git rev-parse HEAD
Write-Host "Tagging commit: $commit"

# Create the specific version tag
git tag $Version
if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create tag $Version"; exit 1 }

# Move the floating major tag
git tag -f $majorTag
if ($LASTEXITCODE -ne 0) { Write-Error "Failed to move tag $majorTag"; exit 1 }

# Push both tags
git push origin $Version
if ($LASTEXITCODE -ne 0) { exit 1 }

git push origin $majorTag --force
if ($LASTEXITCODE -ne 0) { exit 1 }

# Create a GitHub release for the specific version tag
$notesArgs = if ($Notes) { @('--notes', $Notes) } else { @('--generate-notes') }
gh release create $Version --title $Version @notesArgs
if ($LASTEXITCODE -ne 0) { exit 1 }

# Update the major tag release to point to the new tag
gh release edit $majorTag --tag $majorTag --title $majorTag --notes "Latest $majorTag release — see $Version for details." 2>$null
if ($LASTEXITCODE -ne 0) {
    # Major tag release may not exist yet
    gh release create $majorTag --title $majorTag --notes "Latest $majorTag release — see $Version for details."
}

Write-Host ""
Write-Host "Released $Version and moved $majorTag"
