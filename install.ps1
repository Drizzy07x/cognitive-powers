[CmdletBinding()]
param(
    [ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$ReleaseRef = "v1.8.1"
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

function Assert-Python {
    # Get-Command is satisfied by the Windows "App execution alias" at
    # WindowsApps\python.exe, which is a stub: it resolves, then exits without
    # running Python and offers the Microsoft Store instead. Resolving the name
    # therefore proves nothing. Preflight has to run the interpreter, because
    # the only other place this script needs it is the canonical verifier at the
    # very end -- so a stub would be discovered after the profile was mutated,
    # and reported as a rollback rather than as a missing interpreter.
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
    & $Name "-c" "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 3)"
    if ($LASTEXITCODE -eq 3) {
        throw "Cognitive Powers requires Python 3.11 or newer; '$Name' reports an older version. Install a newer interpreter and verify with '$Name --version'."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Required command '$Name' resolves but does not run (exit code $LASTEXITCODE). On Windows the Microsoft Store alias at WindowsApps\python.exe is such a stub: install Python 3.11 or newer, or disable the alias under Settings > Apps > Advanced app settings > App execution aliases, then verify with '$Name --version'."
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
Assert-Python "python"
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
    # A failed transaction restores the previous installation from a recovery
    # marketplace under LocalApplicationData and preserves it. That state is
    # this installer's own product, so a rerun must recognize it and proceed --
    # re-pointing the marketplace at the new immutable SHA -- instead of
    # refusing the very recovery it created. Recognition is deliberately
    # narrow: the exact directory shape the transaction writes, nothing else.
    $recoveryParent = Join-Path ([Environment]::GetFolderPath("LocalApplicationData", "Create")) "cognitive-powers"
    $configuredSourceIsRecoveryMarketplace = $false
    if (
        -not [string]::IsNullOrWhiteSpace($configuredSource) -and
        $configuredSource -notmatch '://' -and
        [IO.Path]::IsPathRooted([string]$configuredSource)
    ) {
        $fullSource = [IO.Path]::GetFullPath([string]$configuredSource)
        $sourceLeaf = Split-Path -Path $fullSource -Leaf
        $rollbackDirectory = Split-Path -Path $fullSource -Parent
        $rollbackLeaf = if ($rollbackDirectory) { Split-Path -Path $rollbackDirectory -Leaf } else { "" }
        $rollbackParent = if ($rollbackDirectory) { Split-Path -Path $rollbackDirectory -Parent } else { "" }
        $configuredSourceIsRecoveryMarketplace = (
            $sourceLeaf -eq "marketplace" -and
            $rollbackLeaf -match '^rollback-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' -and
            $rollbackParent -eq [IO.Path]::GetFullPath($recoveryParent) -and
            (Test-Path -LiteralPath (Join-Path $fullSource ".agents/plugins/marketplace.json") -PathType Leaf)
        )
    }
    if (
        [string]::IsNullOrWhiteSpace($configuredSource) -or
        (
            $allowedSources -notcontains $configuredSource -and
            -not $configuredSourceIsPinnedRepository -and
            -not $configuredSourceIsRecoveryMarketplace
        )
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

# On Unix GetFolderPath verifies the directory and returns an empty string when
# it is missing, and a profile that has never been written to has no
# ~/.local/share yet. Create materializes it and returns the path, which is
# where the rollback copy has to live anyway; without it Join-Path refuses the
# empty string and the installer dies before it can prepare any recovery.
$rollbackBase = Join-Path ([Environment]::GetFolderPath("LocalApplicationData", "Create")) "cognitive-powers"
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
        "The previous installation was restored from recovery marketplace '$rollbackMarketplace'; keep that directory until a remote immutable marketplace is re-established. Re-running this installer recognizes that recovery marketplace and resumes the upgrade from it."
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
