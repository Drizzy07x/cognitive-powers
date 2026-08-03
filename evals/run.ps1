<#
.SYNOPSIS
    Run the Cognitive Powers skill-activation eval.

.DESCRIPTION
    Wraps evals/run_activation_eval.py. Every switch maps one-to-one onto the
    Python flag of the same name (-Arm -> --arm, -Reps -> --reps, and so on);
    anything after -- is forwarded unchanged, so a flag added to the runner is
    usable here on the day it lands rather than after this file is edited.

    Nothing here runs offline: each case spawns the real claude CLI and costs
    money. Use -ValidateOnly to check the corpus and see the invocation count
    before spending anything.

.PARAMETER Arm
    Arms to run, comma-separated: -Arm none,instruction,full. Defaults to
    instruction, which is the shipped configuration.
    PowerShell binds an array parameter once, so repeating the switch is an
    error rather than a second value -- the Python flag is repeatable, this is
    not, and the wrapper translates.

.PARAMETER Reps
    Repetitions per case. Defaults to 3, because a single run of a
    non-deterministic decision is a coin flip rather than a measurement.

.PARAMETER Skills
    Workflow names to narrow to. The should-not-fire pool always runs, so an
    activation rate is never reported without its false-positive rate.

.EXAMPLE
    ./evals/run.ps1 -Quick -Arm full

.EXAMPLE
    ./evals/run.ps1 -Full -Arm none,instruction,full -Reps 3 -JsonOutput C:\tmp\activation.json
#>
[CmdletBinding()]
param(
    [ValidateSet('none', 'instruction', 'full')]
    [string[]]$Arm = @('instruction'),

    [ValidateRange(1, 25)]
    [int]$Reps = 3,

    [string[]]$Skills,

    [ValidateRange(1, 4)]
    [int]$Workers,

    [switch]$Quick,

    [switch]$Full,

    [switch]$NoStopWhenDecided,

    [double]$EquivalenceMargin,

    [switch]$ValidateOnly,

    [switch]$KeepTranscripts,

    [string]$Model = 'sonnet',

    [string]$JsonOutput,

    [string]$MarkdownOutput,

    [double]$Floor,

    [double]$MaxFalsePositive,

    [string]$PythonExecutable,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Forwarded
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$evalsRoot = Split-Path -Parent $PSCommandPath
$runner = Join-Path $evalsRoot 'run_activation_eval.py'

# The interpreter is resolved the same way the plugin's own user config
# resolves it: explicitly. On Windows `python3` is a Microsoft Store stub that
# exits without running Python, so a default that guessed would fail every hook
# in the session under test and report the failure as an arm that changed
# nothing.
$python = if ($PythonExecutable) {
    $PythonExecutable
} elseif ($env:COGNITIVE_POWERS_EVAL_PYTHON) {
    $env:COGNITIVE_POWERS_EVAL_PYTHON
} else {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command) {
        throw 'No python found. Pass -PythonExecutable or set COGNITIVE_POWERS_EVAL_PYTHON.'
    }
    $command.Source
}

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner not found at $runner"
}

$arguments = @($runner)
foreach ($name in $Arm) { $arguments += @('--arm', $name) }
$arguments += @('--reps', $Reps, '--model', $Model)
if ($Skills) { $arguments += @('--skills', ($Skills -join ',')) }
if ($PSBoundParameters.ContainsKey('Workers')) { $arguments += @('--workers', $Workers) }
if ($Quick) { $arguments += '--quick' }
if ($Full) { $arguments += '--full' }
if ($NoStopWhenDecided) { $arguments += '--no-stop-when-decided' }
if ($PSBoundParameters.ContainsKey('EquivalenceMargin')) {
    $arguments += @('--equivalence-margin', $EquivalenceMargin)
}
if ($ValidateOnly) { $arguments += '--validate-only' }
if ($KeepTranscripts) { $arguments += '--keep-transcripts' }
if ($JsonOutput) { $arguments += @('--json-output', $JsonOutput) }
if ($MarkdownOutput) { $arguments += @('--markdown-output', $MarkdownOutput) }
if ($PSBoundParameters.ContainsKey('Floor')) { $arguments += @('--floor', $Floor) }
if ($PSBoundParameters.ContainsKey('MaxFalsePositive')) {
    $arguments += @('--max-false-positive', $MaxFalsePositive)
}
if ($PythonExecutable) { $arguments += @('--python-executable', $PythonExecutable) }
if ($Forwarded) { $arguments += $Forwarded }

& $python @arguments
exit $LASTEXITCODE
