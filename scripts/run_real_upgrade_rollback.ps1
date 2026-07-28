[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$ReleaseRef,
    [Parameter(Mandatory)]
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $root "install.ps1"
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$candidateCommit = (& git -C $root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $candidateCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Unable to resolve the candidate commit."
}

function Use-IsolatedProfile {
    param([Parameter(Mandatory)][string]$Name)
    $codexProfile = Join-Path $output $Name
    $env:CODEX_HOME = Join-Path $codexProfile ".codex"
    $env:HOME = $codexProfile
    $env:USERPROFILE = $codexProfile
    # Codex refuses to load a configuration when CODEX_HOME names a path that
    # does not exist, so the isolated home must exist before any codex command.
    New-Item -ItemType Directory -Force -Path $env:CODEX_HOME | Out-Null
    return $codexProfile
}

function Get-MarketplaceRoot {
    $state = & codex plugin marketplace list --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Unable to list marketplaces." }
    $entry = @($state.marketplaces | Where-Object { $_.name -eq "cognitive-powers" })
    if ($entry.Count -ne 1 -or [string]::IsNullOrWhiteSpace($entry[0].root)) {
        throw "Expected exactly one cognitive-powers marketplace."
    }
    return [IO.Path]::GetFullPath([string]$entry[0].root)
}

function Invoke-CanonicalVerifier {
    param(
        [Parameter(Mandatory)][string]$MarketplaceRoot,
        [Parameter(Mandatory)][string]$Tag,
        [Parameter(Mandatory)][string]$Destination
    )
    $verifier = Join-Path $root "scripts/verify_installed.py"
    $report = & python $verifier --source-root $MarketplaceRoot --installed-root $MarketplaceRoot --tag $Tag
    if ($LASTEXITCODE -ne 0) { throw "Canonical verifier rejected $Tag." }
    [IO.File]::WriteAllText(
        $Destination,
        (($report -join "`n") + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    return (($report -join "`n") | ConvertFrom-Json)
}

Use-IsolatedProfile "Cognitive Powers ü upgrade" | Out-Null
& $installer -ReleaseRef "v1.5.2"
& $installer -ReleaseRef $ReleaseRef
$upgradeRoot = Get-MarketplaceRoot
$upgradeReportPath = Join-Path $output "upgrade-installation-verification.json"
$upgradeReport = Invoke-CanonicalVerifier -MarketplaceRoot $upgradeRoot -Tag $ReleaseRef -Destination $upgradeReportPath
if (-not $upgradeReport.matched -or $upgradeReport.commit -ne $candidateCommit) {
    throw "Upgrade did not end at the exact candidate commit."
}

Use-IsolatedProfile "Cognitive Powers ü rollback" | Out-Null
& $installer -ReleaseRef "v1.5.2"
$previousRoot = Get-MarketplaceRoot
$previousCommit = (& git -C $previousRoot rev-parse HEAD).Trim()
$realPath = $env:PATH
$wrapper = Join-Path $output "failing-python"
New-Item -ItemType Directory -Force -Path $wrapper | Out-Null
# The fault under test is a verifier that fails after the profile has been
# mutated, so that rollback has something to undo. An interpreter that fails
# unconditionally no longer reaches it: preflight runs "python -c" first and
# aborts before any mutation, which is exactly what it was added to do. The
# shim therefore answers the preflight probe and fails only the verifier call.
if ($IsWindows) {
    [IO.File]::WriteAllText(
        (Join-Path $wrapper "python.cmd"),
        "@echo off`r`nif `"%1`"==`"-c`" exit /b 0`r`nexit /b 91`r`n",
        [Text.Encoding]::ASCII
    )
}
else {
    $shim = Join-Path $wrapper "python"
    [IO.File]::WriteAllText(
        $shim,
        "#!/bin/sh`nif [ `"`$1`" = `"-c`" ]; then exit 0; fi`nexit 91`n",
        [Text.Encoding]::ASCII
    )
    & chmod +x $shim
    if ($LASTEXITCODE -ne 0) { throw "Unable to prepare verifier fault shim." }
}
$env:PATH = "$wrapper$([IO.Path]::PathSeparator)$realPath"
$failedAsExpected = $false
try {
    & $installer -ReleaseRef $ReleaseRef
}
catch {
    $failedAsExpected = $_.Exception.Message -match "Installation of $([regex]::Escape($ReleaseRef)) failed"
}
finally {
    $env:PATH = $realPath
}
if (-not $failedAsExpected) { throw "Expected verifier fault was not observed." }

$rollbackRoot = Get-MarketplaceRoot
$rollbackReportPath = Join-Path $output "rollback-installation-verification.json"
$rollbackReport = Invoke-CanonicalVerifier -MarketplaceRoot $rollbackRoot -Tag "v1.5.2" -Destination $rollbackReportPath
if (-not $rollbackReport.matched -or $rollbackReport.commit -ne $previousCommit) {
    throw "Rollback did not restore the exact previous immutable release."
}

$evidence = [ordered]@{
    schemaVersion = 1
    product = "cognitive-powers"
    candidateCommit = $candidateCommit
    candidateTag = $ReleaseRef
    scenarios = [ordered]@{
        "upgrade-v1.5.2" = [ordered]@{ passed = $true; finalTag = $ReleaseRef; finalCommit = $candidateCommit }
        rollback = [ordered]@{ passed = $true; finalTag = "v1.5.2"; finalCommit = $previousCommit }
        "unicode-space-path" = [ordered]@{ passed = $true; finalTag = $ReleaseRef; finalCommit = $candidateCommit }
    }
}
$evidencePath = Join-Path $output "upgrade-rollback-evidence.json"
[IO.File]::WriteAllText(
    $evidencePath,
    (($evidence | ConvertTo-Json -Depth 8) + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Write-Host ($evidence | ConvertTo-Json -Depth 8 -Compress)
