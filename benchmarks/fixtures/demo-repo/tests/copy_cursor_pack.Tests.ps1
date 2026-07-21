Describe 'PowerShell cursor wildcard copy with paths containing spaces' {
    BeforeEach {
        $source = Join-Path $TestDrive 'source pack'
        $destination = Join-Path $TestDrive 'installed cursors'
        New-Item -ItemType Directory -Path $source | Out-Null
        Set-Content -LiteralPath (Join-Path $source 'arrow.cur') -Value 'cursor'
        Set-Content -LiteralPath (Join-Path $source 'busy.ani') -Value 'animated'
        Set-Content -LiteralPath (Join-Path $source 'notes.txt') -Value 'ignore'
    }

    It 'copies cursor wildcards and preserves paths containing spaces' {
        $scriptPath = Join-Path $PSScriptRoot '..\scripts\copy_cursor_pack.ps1'
        & $scriptPath -SourceDirectory $source -DestinationDirectory $destination

        Test-Path -LiteralPath (Join-Path $destination 'arrow.cur') | Should -BeTrue
        Test-Path -LiteralPath (Join-Path $destination 'busy.ani') | Should -BeTrue
        Test-Path -LiteralPath (Join-Path $destination 'notes.txt') | Should -BeFalse
    }
}
