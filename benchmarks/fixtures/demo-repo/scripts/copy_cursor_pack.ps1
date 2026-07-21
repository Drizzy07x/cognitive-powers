param(
    [Parameter(Mandatory = $true)]
    [string] $SourceDirectory,

    [Parameter(Mandatory = $true)]
    [string] $DestinationDirectory
)

# PowerShell expands the source wildcard while LiteralPath preserves destination paths with spaces.
New-Item -ItemType Directory -Force -Path $DestinationDirectory | Out-Null
$sourcePattern = Join-Path -Path $SourceDirectory -ChildPath '*.cur'
Copy-Item -Path $sourcePattern -Destination $DestinationDirectory -Force

$animatedPattern = Join-Path -Path $SourceDirectory -ChildPath '*.ani'
Copy-Item -Path $animatedPattern -Destination $DestinationDirectory -Force

Get-ChildItem -LiteralPath $DestinationDirectory
