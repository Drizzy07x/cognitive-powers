[CmdletBinding()]
param(
    [ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$ReleaseRef = "v1.7.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repository = "Drizzy07x/cognitive-powers"
$marketplace = "cognitive-powers"
$personalMarketplaceName = "personal"
$pluginId = "cognitive-powers@cognitive-powers"
$personalPluginId = "cognitive-powers@personal"
$pluginName = "cognitive-powers"
$releaseRef = $ReleaseRef
$expectedVersion = $releaseRef.Substring(1)
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
$allowedPreviousPluginIds = @($pluginId, $personalPluginId)

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

function Invoke-CodexBestEffort {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & codex @Arguments 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $priorPreference
    }
}

function Read-CodexJsonBestEffort {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $raw = & codex @Arguments 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    }
    catch {
        return $null
    }
    finally {
        $ErrorActionPreference = $priorPreference
    }
}

Assert-Command "gh"
Assert-Command "codex"
Assert-Command "python"
Invoke-Checked "gh" @("auth", "status", "--hostname", "github.com")
Invoke-Checked "gh" @("auth", "setup-git", "--hostname", "github.com")
Invoke-Checked "gh" @("api", "repos/$repository/git/ref/tags/$releaseRef", "--silent")
$releaseCommitRaw = & gh api "repos/$repository/commits/$releaseRef"
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve immutable release commit for '$releaseRef'." }
try {
    $releaseCommitResponse = $releaseCommitRaw | ConvertFrom-Json -ErrorAction Stop
    $releaseCommit = [string]$releaseCommitResponse.sha
}
catch {
    throw "GitHub returned invalid JSON while resolving '$releaseRef'."
}
if ($releaseCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Release '$releaseRef' did not resolve to a full commit SHA."
}

$marketplaceState = (& codex plugin marketplace list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Unable to read configured Codex marketplaces." }
$configured = @($marketplaceState.marketplaces | Where-Object { $_.name -eq $marketplace })
$personalMarketplace = @($marketplaceState.marketplaces | Where-Object { $_.name -eq $personalMarketplaceName })
if ($configured.Count -gt 1) { throw "More than one marketplace named '$marketplace' is configured." }
if ($configured.Count -eq 1) {
    $configuredSource = $configured[0].marketplaceSource.source
    $configuredSourceIsPinnedRepository = (
        -not [string]::IsNullOrWhiteSpace($configuredSource) -and
        $configuredSource -match "^$([regex]::Escape($repository))@(v\d+\.\d+\.\d+|[0-9a-f]{40})$"
    )
    if (
        [string]::IsNullOrWhiteSpace($configuredSource) -or
        ($allowedSources -notcontains $configuredSource -and -not $configuredSourceIsPinnedRepository)
    ) {
        throw "Marketplace '$marketplace' already points to '$configuredSource', not '$repository'."
    }
}

$preInstallState = (& codex plugin list --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect existing Codex plugins." }
$duplicates = @($preInstallState.installed | Where-Object { $_.name -eq $pluginName -and $_.installed })
$unknownDuplicates = @($duplicates | Where-Object { $allowedPreviousPluginIds -notcontains $_.pluginId })
if ($unknownDuplicates.Count -ne 0) { throw "An unrecognized Cognitive Powers installation cannot be restored safely." }
if (@($duplicates | Group-Object pluginId | Where-Object { $_.Count -gt 1 }).Count -ne 0) {
    throw "Duplicate plugin identifiers cannot be restored unambiguously."
}
if (@($duplicates | Where-Object { -not $_.enabled }).Count -ne 0) {
    throw "A disabled prior installation cannot be restored exactly; refusing to mutate it."
}
$previousReleaseCommit = $null
if ($configured.Count -eq 1) {
    $configuredSource = [string]$configured[0].marketplaceSource.source
    if ($configuredSource -match "^$([regex]::Escape($repository))@([0-9a-f]{40})$") {
        $previousReleaseCommit = $Matches[1]
    }
    elseif ($configuredSource -match "^$([regex]::Escape($repository))@(v\d+\.\d+\.\d+)$") {
        $previousRef = $Matches[1]
        $previousCommitRaw = & gh api "repos/$repository/commits/$previousRef"
        if ($LASTEXITCODE -ne 0) { throw "Unable to resolve the previous immutable release '$previousRef'." }
        try {
            $previousReleaseCommit = [string](($previousCommitRaw | ConvertFrom-Json -ErrorAction Stop).sha)
        }
        catch {
            throw "GitHub returned invalid JSON while resolving the previous release."
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($configured[0].root)) {
        $previousReleaseCommit = (& git -C $configured[0].root rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0) { $previousReleaseCommit = ([string]$previousReleaseCommit).Trim() }
    }
    if ($previousReleaseCommit -notmatch '^[0-9a-f]{40}$') {
        throw "The previous marketplace cannot be bound to an immutable commit; refusing to mutate it."
    }
}
$privatePrevious = @($duplicates | Where-Object { $_.pluginId -eq $pluginId })
$personalPrevious = @($duplicates | Where-Object { $_.pluginId -eq $personalPluginId })
if ($privatePrevious.Count -ne 0 -and $configured.Count -ne 1) {
    throw "The private plugin has no configured marketplace to back up; refusing to mutate it."
}
if ($personalPrevious.Count -ne 0) {
    if ($personalMarketplace.Count -ne 1 -or [string]::IsNullOrWhiteSpace($personalMarketplace[0].root) -or -not (Test-Path -LiteralPath $personalMarketplace[0].root -PathType Container)) {
        throw "The personal plugin marketplace is unavailable; refusing to mutate it."
    }
}

$rollbackBase = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "cognitive-powers"
$rollbackRoot = Join-Path $rollbackBase "rollback-$([guid]::NewGuid())"
$rollbackMarketplace = Join-Path $rollbackRoot "marketplace"
$rollbackPrepared = $false
$preserveRollback = $false
$mutationStarted = $false

try {
    if ($configured.Count -eq 1) {
        if ([string]::IsNullOrWhiteSpace($configured[0].root) -or -not (Test-Path -LiteralPath $configured[0].root -PathType Container)) {
            throw "Configured marketplace root is unavailable; refusing to mutate the installation."
        }
        New-Item -ItemType Directory -Path $rollbackRoot | Out-Null
        Copy-Item -LiteralPath $configured[0].root -Destination $rollbackMarketplace -Recurse -Force
        if (-not (Test-Path -LiteralPath (Join-Path $rollbackMarketplace ".agents/plugins/marketplace.json") -PathType Leaf)) {
            throw "Marketplace rollback copy is incomplete; refusing to mutate the installation."
        }
        $rollbackPrepared = $true
    }

    foreach ($previous in $privatePrevious) {
        $mutationStarted = $true
        Invoke-Checked "codex" @("plugin", "remove", $previous.pluginId, "--json")
    }
    if ($configured.Count -eq 1) {
        $mutationStarted = $true
        Invoke-Checked "codex" @("plugin", "marketplace", "remove", $marketplace, "--json")
    }
    $mutationStarted = $true
    Invoke-Checked "codex" @("plugin", "marketplace", "add", $repository, "--ref", $releaseCommit, "--json")
    Invoke-Checked "codex" @("plugin", "add", $pluginId, "--json")

    $provisionalState = (& codex plugin list --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw "Unable to verify the provisional installation." }
    $provisionalMatches = @($provisionalState.installed | Where-Object {
        $_.pluginId -eq $pluginId -and $_.installed -and $_.enabled -and $_.version -eq $expectedVersion
    })
    if ($provisionalMatches.Count -ne 1) { throw "The provisional private installation is invalid." }

    foreach ($previous in $personalPrevious) {
        Invoke-Checked "codex" @("plugin", "remove", $previous.pluginId, "--json")
    }

    $pluginState = (& codex plugin list --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw "Unable to verify installed Codex plugins." }
    $enabledMatches = @($pluginState.installed | Where-Object {
        $_.name -eq $pluginName -and $_.installed -and $_.enabled
    })
    if ($enabledMatches.Count -ne 1 -or $enabledMatches[0].pluginId -ne $pluginId -or $enabledMatches[0].version -ne $expectedVersion) {
        throw "Expected exactly one enabled '$pluginName' plugin at version '$expectedVersion': '$pluginId'."
    }
    $installedMarketplaceState = (& codex plugin marketplace list --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve the installed marketplace root." }
    $installedMarketplace = @($installedMarketplaceState.marketplaces | Where-Object { $_.name -eq $marketplace })
    if ($installedMarketplace.Count -ne 1 -or [string]::IsNullOrWhiteSpace($installedMarketplace[0].root)) {
        throw "Expected exactly one installed marketplace root for '$marketplace'."
    }
    $installedMarketplaceRoot = [System.IO.Path]::GetFullPath([string]$installedMarketplace[0].root)
    $verifier = Join-Path $PSScriptRoot "scripts/verify_installed.py"
    Invoke-Checked "python" @(
        $verifier,
        "--source-root", $installedMarketplaceRoot,
        "--installed-root", $installedMarketplaceRoot,
        "--tag", $releaseRef
    )
}
catch {
    $installFailure = $_
    $rollbackSucceeded = -not $mutationStarted
    $restoredFromRemote = $false
    if ($mutationStarted) {
        [void](Invoke-CodexBestEffort @("plugin", "remove", $pluginId, "--json"))
        $targetMarketplaceRemoved = Invoke-CodexBestEffort @("plugin", "marketplace", "remove", $marketplace, "--json")
        if ($configured.Count -eq 1 -and $null -ne $previousReleaseCommit -and $targetMarketplaceRemoved) {
            $restoredFromRemote = Invoke-CodexBestEffort @(
                "plugin", "marketplace", "add", $repository,
                "--ref", $previousReleaseCommit, "--json"
            )
        }
        if ($configured.Count -eq 1 -and $rollbackPrepared -and -not $restoredFromRemote) {
            [void](Invoke-CodexBestEffort @("plugin", "marketplace", "add", $rollbackMarketplace, "--json"))
        }
        foreach ($previous in $duplicates) {
            [void](Invoke-CodexBestEffort @("plugin", "add", $previous.pluginId, "--json"))
        }

        $rollbackSucceeded = $true
        $restoredState = Read-CodexJsonBestEffort @("plugin", "list", "--json")
        if ($null -eq $restoredState) {
            $rollbackSucceeded = $false
        }
        else {
            $restoredMatches = @($restoredState.installed | Where-Object { $_.name -eq $pluginName -and $_.installed })
            if ($restoredMatches.Count -ne $duplicates.Count) { $rollbackSucceeded = $false }
            foreach ($previous in $duplicates) {
                $restored = @($restoredMatches | Where-Object {
                    $_.pluginId -eq $previous.pluginId -and $_.enabled -eq $previous.enabled -and $_.version -eq $previous.version
                })
                if ($restored.Count -ne 1) { $rollbackSucceeded = $false }
            }
        }

        $restoredMarketplaces = Read-CodexJsonBestEffort @("plugin", "marketplace", "list", "--json")
        if ($null -eq $restoredMarketplaces) {
            $rollbackSucceeded = $false
        }
        else {
            $restoredMarketplace = @($restoredMarketplaces.marketplaces | Where-Object { $_.name -eq $marketplace })
            if ($configured.Count -eq 1) {
                if ($restoredMarketplace.Count -ne 1 -or [string]::IsNullOrWhiteSpace($restoredMarketplace[0].root)) {
                    $rollbackSucceeded = $false
                }
                elseif ($restoredFromRemote) {
                    $restoredSource = [string]$restoredMarketplace[0].marketplaceSource.source
                    $actualRevision = (& git -C $restoredMarketplace[0].root rev-parse HEAD 2>$null)
                    if ($LASTEXITCODE -ne 0) {
                        $rollbackSucceeded = $false
                    }
                    elseif (
                        $restoredSource -ne "$repository@$previousReleaseCommit" -or
                        ([string]$actualRevision).Trim() -ne $previousReleaseCommit
                    ) {
                        $rollbackSucceeded = $false
                    }
                }
                else {
                    $expectedRoot = [System.IO.Path]::GetFullPath($rollbackMarketplace)
                    $actualRoot = [System.IO.Path]::GetFullPath($restoredMarketplace[0].root)
                    if ($actualRoot -ne $expectedRoot) { $rollbackSucceeded = $false }
                }
            }
            elseif ($restoredMarketplace.Count -ne 0) {
                $rollbackSucceeded = $false
            }
        }
    }

    if ($rollbackPrepared -and $mutationStarted -and -not $restoredFromRemote) { $preserveRollback = $true }
    $rollbackMessage = if ($rollbackSucceeded -and $preserveRollback) {
        "The previous installation was restored from recovery marketplace '$rollbackMarketplace'; keep that directory for manual recovery until a remote immutable marketplace is re-established."
    }
    elseif ($rollbackSucceeded) { "The previous installation was restored." }
    elseif ($rollbackPrepared) { "Automatic rollback was incomplete. Recovery marketplace preserved at '$rollbackMarketplace'." }
    else { "No complete rollback copy was available." }
    throw "Installation of $releaseRef failed. $rollbackMessage Original error: $($installFailure.Exception.Message)"
}
finally {
    if ((Test-Path -LiteralPath $rollbackRoot) -and -not $preserveRollback) {
        Remove-Item -LiteralPath $rollbackRoot -Recurse -Force
    }
}

Write-Host "Cognitive Powers $expectedVersion is installed and enabled from immutable ref $releaseRef. Restart Codex before starting a new task."
