[CmdletBinding()]
param(
    [ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$ReleaseRef = "v1.9.2",
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    # The default is printed from the parameter, never spelled again here: a
    # second copy of the tag is a second thing for bump_version.py to miss, and
    # help text is where nobody would notice it had gone stale.
    Write-Host @"
usage: install.ps1 [-ReleaseRef vX.Y.Z]

  -ReleaseRef   release tag to install (default: $ReleaseRef)

Environment:
  COGNITIVE_POWERS_PYTHON   interpreter to use instead of 'python'

Run this script from its own checkout: the canonical verifier is resolved beside
it, and a copy separated from scripts/verify_installed.py has no postcondition.
"@
    exit 0
}

# The Microsoft Store alias hijacks the bare name 'python' on a profile that has
# never installed a real interpreter, and disabling it is a Settings change the
# operator may not be able to make. The override names a working interpreter
# without touching the profile, and matches COGNITIVE_POWERS_PYTHON in install.sh
# so one documented variable works on either host.
$pythonCommand = if ([string]::IsNullOrWhiteSpace($env:COGNITIVE_POWERS_PYTHON)) {
    "python"
}
else {
    $env:COGNITIVE_POWERS_PYTHON
}

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

function Get-RecoveryParent {
    # DIVERGENCE (profile location): off Windows this spells out
    # "${XDG_DATA_HOME:-$HOME/.local/share}", which is what install.sh spells
    # out too, so that a recovery marketplace written by either installer is
    # recognized by both. Leaving the answer to .NET made that true only on
    # Linux: on macOS SpecialFolder.LocalApplicationData is
    # Library/Application Support under the account's own home, and it consults
    # neither variable -- so the two installers wrote their recovery copies to
    # different directories and each refused to resume from the other's. It also
    # meant the transaction suite, which overrides HOME to keep that copy inside
    # its fixture, was in fact writing into the developer's real profile and
    # accumulating rollback directories there across runs.
    $parent = if ($IsWindows) {
        # Windows keeps the .NET answer: it is the only rule that exists there,
        # and no POSIX installer competes for the directory. "Create" costs
        # nothing on a real profile and returns a path rather than the empty
        # string Join-Path refuses if the folder is somehow absent.
        Join-Path ([Environment]::GetFolderPath("LocalApplicationData", "Create")) "cognitive-powers"
    }
    else {
        $dataHome = if (-not [string]::IsNullOrWhiteSpace($env:XDG_DATA_HOME)) {
            $env:XDG_DATA_HOME
        }
        elseif (-not [string]::IsNullOrWhiteSpace($env:HOME)) {
            Join-Path $env:HOME ".local/share"
        }
        else {
            throw "Neither XDG_DATA_HOME nor HOME names a data directory, so there is nowhere to keep a recovery marketplace."
        }
        Join-Path $dataHome "cognitive-powers"
    }
    # Materialized here rather than at the point of use, because the recognition
    # check canonicalizes this path and comparing an unresolvable path against a
    # resolved one is a mismatch whatever the two actually name. A profile that
    # has never been written to has no ~/.local/share yet, and the recovery copy
    # has to live somewhere.
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    return $parent
}

function Assert-Verifier {
    # The postcondition needs a file that ships beside this script, and its
    # absence is knowable now -- so it is checked here, for the same reason
    # Assert-Python runs here: discovered at the end instead, it is reported as
    # a failed installation and a rollback rather than as a missing file.
    #
    # $PSScriptRoot is empty when this source is run as a scriptblock instead of
    # as a file, which is what the README's one-liner did. The whole transaction
    # then completed and Join-Path threw "Cannot bind argument to parameter
    # 'Path' because it is an empty string" -- so the installation was rolled
    # back and the reported cause named an empty string rather than a verifier
    # that was never fetched. CI ran the script as a file and never saw it.
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw "This installer has no script path of its own, so the canonical verifier beside it cannot be located. Run install.ps1 as a file from its own checkout rather than as a scriptblock or piped source."
    }
    $verifier = Join-Path $PSScriptRoot "scripts/verify_installed.py"
    if (-not (Test-Path -LiteralPath $verifier -PathType Leaf)) {
        throw "The canonical verifier is missing at '$verifier'. Run install.ps1 from a complete checkout of the release."
    }
}

Assert-Command "gh"
Assert-Command "codex"
Assert-Python $pythonCommand
Assert-Verifier
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
$recoveryParent = Get-RecoveryParent
if ($configured.Count -eq 1) {
    $configuredSource = $configured[0].marketplaceSource.source
    $configuredSourceIsPinnedRepository = (
        -not [string]::IsNullOrWhiteSpace($configuredSource) -and
        $configuredSource -match "^$([regex]::Escape($repository))@(v\d+\.\d+\.\d+|[0-9a-f]{40})$"
    )
    # A failed transaction restores the previous installation from a recovery
    # marketplace under the data home and preserves it. That state is this
    # installer's own product, so a rerun must recognize it and proceed --
    # re-pointing the marketplace at the new immutable SHA -- instead of
    # refusing the very recovery it created. Recognition is deliberately
    # narrow: the exact directory shape the transaction writes, nothing else.
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

# The same directory the preflight recognized a preserved recovery under, taken
# from the one resolver: a rollback written somewhere the recognition check does
# not look would stop being resumable the moment it was needed.
$rollbackRoot = Join-Path $recoveryParent "rollback-$([guid]::NewGuid())"
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
    Invoke-Checked $pythonCommand @(
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

    # Kept whenever the rollback did not verify, not only when the profile was
    # pointed back at this copy. Reading "the remote took over" from the attempt
    # rather than from the verification meant a restore that came back on the
    # wrong revision, or left the plugin inventory short, deleted the recovery
    # copy while the failure message still told the operator to keep it -- so
    # the one case where recovery material matters most was the one that had
    # none, and the advice named a directory that was already gone.
    if (
        $rollbackPrepared -and $mutationStarted -and
        (-not $restoredFromRemote -or -not $rollbackSucceeded)
    ) {
        $preserveRollback = $true
    }
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
