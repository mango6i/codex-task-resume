# Shared by the installer and project build. User data is outside these boundaries.
function Assert-TaskPath {
    param([string]$Path, [string]$Boundary)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $base = [IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/')
    if (-not $full.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target is outside its boundary: $full"
    }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path refused: $cursor" }
        }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

function Remove-TaskArtifact {
    param([string]$Path, [string]$Boundary)
    $full = Assert-TaskPath -Path $Path -Boundary $Boundary
    if (-not (Test-Path -LiteralPath $full)) { return }
    $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer) {
        $linked = Get-ChildItem -LiteralPath $full -Force -Recurse -ErrorAction Stop |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1
        if ($linked) { throw "Linked content refused: $($linked.FullName)" }
    }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
}

function Test-TaskPlugin {
    param([string]$Path, [string]$Version = '')
    foreach ($relative in @('.codex-plugin\plugin.json', 'plugin.json')) {
        $manifestPath = Join-Path $Path $relative
        if (Test-Path -LiteralPath $manifestPath) {
            try {
                $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
                return ($manifest.name -eq 'skillhub-quota-resume' -and (-not $Version -or $manifest.version -eq $Version))
            } catch { return $false }
        }
    }
    return $false
}

function Clear-OldTaskInstalls {
    param([string]$PluginBase, [string]$CurrentSource, [string]$CacheBase, [string]$Version)
    # Call only after a successful native install. Name AND identity must match.
    $sourceFull = Assert-TaskPath -Path $CurrentSource -Boundary $PluginBase
    if (-not (Test-TaskPlugin -Path $sourceFull -Version $Version)) { throw 'Current source is not verified.' }
    $removed = 0
    foreach ($item in @(Get-ChildItem -LiteralPath $PluginBase -Directory -Force)) {
        if ($item.FullName -eq $sourceFull) { continue }
        if ($item.Name -notmatch '^(skillhub-quota-resume|task-resume)(-\d+(\.\d+)*(\-(native|modal|polished))?)?(\.backup-\d{8}-\d{6}-\d{3})?$') { continue }
        try {
            $null = Assert-TaskPath -Path $item.FullName -Boundary $PluginBase
            if (Test-TaskPlugin -Path $item.FullName) {
                Remove-TaskArtifact -Path $item.FullName -Boundary $PluginBase
                $removed++
            }
        } catch { Write-Warning "Old plugin copy retained: $($item.Name). $($_.Exception.Message)" }
    }
    # A verified current cache must exist before deleting old cache versions.
    if (Test-Path -LiteralPath $CacheBase) {
        $currentCache = Join-Path $CacheBase $Version
        $null = Assert-TaskPath -Path $currentCache -Boundary $CacheBase
        if (Test-TaskPlugin -Path $currentCache -Version $Version) {
            foreach ($item in @(Get-ChildItem -LiteralPath $CacheBase -Directory -Force)) {
                if ($item.Name -eq $Version -or $item.Name -notmatch '^\d+\.\d+\.\d+(\.backup-\d{8}-\d{6}-\d{3})?$') { continue }
                try {
                    $null = Assert-TaskPath -Path $item.FullName -Boundary $CacheBase
                    if (Test-TaskPlugin -Path $item.FullName) {
                        Remove-TaskArtifact -Path $item.FullName -Boundary $CacheBase
                        $removed++
                    }
                } catch { Write-Warning "Old plugin cache retained: $($item.Name). $($_.Exception.Message)" }
            }
        } else { Write-Warning 'Current native cache could not be verified; old cache versions were retained.' }
    }
    Write-Output "Removed $removed obsolete plugin copies."
}
