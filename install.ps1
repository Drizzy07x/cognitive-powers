[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repository = "Drizzy07x/cognitive-powers"
$marketplace = "cognitive-powers"
$pluginId = "cognitive-powers@cognitive-powers"
$expectedVersion = "1.4.2"

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command $($Arguments -join ' ')"
    }
}

Assert-Command "gh"
Assert-Command "codex"

Invoke-Checked "gh" @("auth", "status", "--hostname", "github.com")
Invoke-Checked "gh" @("auth", "setup-git", "--hostname", "github.com")

$marketplaceState = (& codex plugin marketplace list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read configured Codex marketplaces."
}

$configured = $marketplaceState.marketplaces | Where-Object { $_.name -eq $marketplace }
if ($configured) {
    $configuredSource = $configured.marketplaceSource.source
    if ($configuredSource -and $configuredSource -notmatch "(?:^|github\.com[/:])$([regex]::Escape($repository))(?:\.git)?$") {
        throw "Marketplace '$marketplace' already points to '$configuredSource', not '$repository'."
    }
    Invoke-Checked "codex" @("plugin", "marketplace", "upgrade", $marketplace, "--json")
}
else {
    Invoke-Checked "codex" @("plugin", "marketplace", "add", $repository, "--ref", "main", "--json")
}

Invoke-Checked "codex" @("plugin", "add", $pluginId, "--json")

$pluginState = (& codex plugin list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to verify installed Codex plugins."
}

$installed = $pluginState.installed | Where-Object { $_.pluginId -eq $pluginId }
if (-not $installed -or -not $installed.installed -or -not $installed.enabled) {
    throw "Plugin '$pluginId' was not reported as installed and enabled."
}
if ($installed.version -ne $expectedVersion) {
    throw "Plugin '$pluginId' reported version '$($installed.version)', expected '$expectedVersion'."
}

Write-Host "Cognitive Powers $expectedVersion is installed and enabled. Restart Codex before starting a new task."
