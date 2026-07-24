[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repository = "Drizzy07x/cognitive-powers"
$marketplace = "cognitive-powers"
$pluginId = "cognitive-powers@cognitive-powers"
$pluginName = "cognitive-powers"
$expectedVersion = "1.5.2"
$releaseRef = "v$expectedVersion"
$allowedSources = @(
    $repository,
    "$repository@$releaseRef",
    "https://github.com/$repository",
    "https://github.com/$repository.git",
    "git@github.com:$repository",
    "git@github.com:$repository.git",
    "ssh://git@github.com/$repository",
    "ssh://git@github.com/$repository.git"
)

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
Invoke-Checked "gh" @("api", "repos/$repository/git/ref/tags/$releaseRef", "--silent")

$marketplaceState = (& codex plugin marketplace list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read configured Codex marketplaces."
}
$configured = $marketplaceState.marketplaces | Where-Object { $_.name -eq $marketplace }
if ($configured) {
    $configuredSource = $configured.marketplaceSource.source
    if (
        [string]::IsNullOrWhiteSpace($configuredSource) -or
        $allowedSources -notcontains $configuredSource
    ) {
        throw "Marketplace '$marketplace' already points to '$configuredSource', not '$repository'."
    }
}

$preInstallState = (& codex plugin list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect existing Codex plugins."
}
$duplicates = @(
    $preInstallState.installed | Where-Object {
        $_.name -eq $pluginName -and $_.installed
    }
)
foreach ($duplicate in $duplicates) {
    Invoke-Checked "codex" @(
        "plugin", "remove", $duplicate.pluginId, "--json"
    )
}

if ($configured) {
    Invoke-Checked "codex" @("plugin", "marketplace", "remove", $marketplace, "--json")
}
Invoke-Checked "codex" @("plugin", "marketplace", "add", $repository, "--ref", $releaseRef, "--json")
Invoke-Checked "codex" @("plugin", "add", $pluginId, "--json")

$pluginState = (& codex plugin list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to verify installed Codex plugins."
}
$enabledMatches = @(
    $pluginState.installed | Where-Object {
        $_.name -eq $pluginName -and $_.installed -and $_.enabled
    }
)
if ($enabledMatches.Count -ne 1 -or $enabledMatches[0].pluginId -ne $pluginId) {
    throw "Expected exactly one enabled '$pluginName' plugin: '$pluginId'."
}
$installed = $enabledMatches[0]
if (-not $installed.installed -or -not $installed.enabled) {
    throw "Plugin '$pluginId' was not reported as installed and enabled."
}
if ($installed.version -ne $expectedVersion) {
    throw "Plugin '$pluginId' reported version '$($installed.version)', expected '$expectedVersion'."
}

Write-Host "Cognitive Powers $expectedVersion is installed and enabled from immutable ref $releaseRef. Restart Codex before starting a new task."
