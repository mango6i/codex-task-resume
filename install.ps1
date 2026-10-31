param([string]$UserRoot = $env:USERPROFILE)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'maintenance.ps1')
$sourceRoot = $PSScriptRoot
$resolvedUserRoot = [IO.Path]::GetFullPath($UserRoot)
$pluginBase = Join-Path $resolvedUserRoot '.codex\plugins'
$pluginRoot = Join-Path $pluginBase 'task-resume-1.0.0'
$marketPath = Join-Path $resolvedUserRoot '.agents\plugins\marketplace.json'
$sourceManifest = Get-Content -LiteralPath (Join-Path $sourceRoot '.codex-plugin\plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($sourceManifest.name -ne 'skillhub-quota-resume' -or $sourceManifest.version -ne '1.0.0') { throw 'Unexpected plugin identity or version.' }
$null = Assert-TaskPath -Path $pluginRoot -Boundary $pluginBase
$null = Assert-TaskPath -Path $marketPath -Boundary (Join-Path $resolvedUserRoot '.agents\plugins')
$sourceFull = [IO.Path]::GetFullPath($sourceRoot).TrimEnd('\', '/')
if ($sourceFull -eq $pluginRoot -or $sourceFull.StartsWith($pluginRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Run the installer from the extracted delivery folder.' }
if ((Test-Path -LiteralPath $pluginRoot) -and -not (Test-TaskPlugin -Path $pluginRoot)) { throw 'Destination is not this plugin; no changes made.' }
$pythonCommand = Get-Command python -ErrorAction Stop
& $pythonCommand.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required; previous installation preserved.' }
$runtimeResult = & $pythonCommand.Source (Join-Path $sourceRoot 'skills\quota-resume\scripts\runtime_paths.py') --check
$runtimeExit = $LASTEXITCODE
$runtimeInfo = $runtimeResult | ConvertFrom-Json
if ($runtimeExit -ne 0 -or -not $runtimeInfo.path) { throw $runtimeInfo.error }
$codexCommand = [PSCustomObject]@{Source = $runtimeInfo.path}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = "$pluginRoot.backup-$stamp"
$stageRoot = Join-Path $pluginBase ('.task-resume-install-' + [Guid]::NewGuid().ToString('N'))
$marketExisted = Test-Path -LiteralPath $marketPath
$marketBytes = $null
if ($marketExisted) {
    $marketBytes = [IO.File]::ReadAllBytes($marketPath)
    $market = Get-Content -LiteralPath $marketPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $market.name -or $null -eq $market.plugins -or $market.name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*$') { throw 'Existing marketplace shape is unsupported; no changes made.' }
} else {
    $market = [PSCustomObject]@{ name = 'skillhub-local'; interface = @{displayName = '本地插件'}; plugins = @() }
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sourceReplaced = $false
$marketWritten = $false
$installedCache = Join-Path $pluginBase ("cache\$($market.name)\skillhub-quota-resume\$($sourceManifest.version)")
$null = Assert-TaskPath -Path $installedCache -Boundary $pluginBase
$cacheBackup = Join-Path $pluginBase ('.task-resume-cache-backup-' + [Guid]::NewGuid().ToString('N'))
$cacheMutationStarted = $false
$cacheBackedUp = $false
try {
    if (Test-Path -LiteralPath $installedCache) {
        if (-not (Test-TaskPlugin -Path $installedCache -Version $sourceManifest.version)) { throw 'Current cache identity is unknown; no changes made.' }
        $null = Assert-TaskPath -Path $cacheBackup -Boundary $pluginBase
        if (Get-ChildItem -LiteralPath $installedCache -Force -Recurse | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1) { throw 'Linked cache content refused.' }
        $cacheBackedUp = $true
        Copy-Item -LiteralPath $installedCache -Destination $cacheBackup -Recurse
    }
    $null = Assert-TaskPath -Path $stageRoot -Boundary $pluginBase
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    foreach ($releaseEntry in @('.codex-plugin', '.mcp.json', 'hooks', 'server', 'skills', 'ui', 'assets',
            'tests', 'scripts', 'docs', 'README.md', 'README_EN.md', 'LICENSE', 'install.cmd', 'install.ps1', 'maintenance.ps1')) {
        $entryPath = Join-Path $sourceRoot $releaseEntry
        if (Test-Path -LiteralPath $entryPath) { Copy-Item -LiteralPath $entryPath -Destination $stageRoot -Recurse -Force }
    }
    $hookPath = Join-Path $stageRoot 'hooks\hooks.json'
    $hooks = Get-Content -LiteralPath $hookPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $pythonLiteral = "'" + $pythonCommand.Source.Replace("'", "''") + "'"
    foreach ($property in $hooks.hooks.PSObject.Properties) {
        foreach ($group in $property.Value) {
            foreach ($handler in $group.hooks) {
                $windowsCommand = "& $pythonLiteral " + '"$env:PLUGIN_ROOT/skills/quota-resume/scripts/hook.py"'
                $handler | Add-Member -NotePropertyName commandWindows -NotePropertyValue $windowsCommand -Force
            }
        }
    }
    [IO.File]::WriteAllText($hookPath, ($hooks | ConvertTo-Json -Depth 20), $utf8)
    $mcpConfig = Get-Content -LiteralPath (Join-Path $stageRoot '.mcp.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($mcpConfig.mcpServers.quota_settings.command -ne 'python') { throw 'Settings command must remain portable.' }
    # Only the installed copy binds to this machine's verified interpreter.
    $mcpConfig.mcpServers.quota_settings.command = $pythonCommand.Source
    $mcpConfig.mcpServers.quota_settings.args = @((Join-Path $pluginRoot 'server\settings_mcp.py'))
    $mcpConfig.mcpServers.quota_settings.cwd = $pluginRoot
    $mcpConfig.mcpServers.quota_settings | Add-Member -NotePropertyName env -NotePropertyValue @{TASK_RESUME_CODEX=$codexCommand.Source} -Force
    [IO.File]::WriteAllText((Join-Path $stageRoot '.mcp.json'), ($mcpConfig | ConvertTo-Json -Depth 20), $utf8)
    & $pythonCommand.Source (Join-Path $stageRoot 'server\i18n.py') --manifest (Join-Path $stageRoot '.codex-plugin\plugin.json')
    if ($LASTEXITCODE -ne 0) { throw 'Plugin display-language preparation failed; previous installation preserved.' }
    if (Test-Path -LiteralPath $pluginRoot) {
        $null = Assert-TaskPath -Path $backupRoot -Boundary $pluginBase
        Move-Item -LiteralPath $pluginRoot -Destination $backupRoot
    }
    Move-Item -LiteralPath $stageRoot -Destination $pluginRoot
    $sourceReplaced = $true
    $entry = [PSCustomObject]@{name='skillhub-quota-resume'; source=@{source='local'; path='./.codex/plugins/task-resume-1.0.0'}; policy=@{installation='AVAILABLE'; authentication='ON_INSTALL'}; category='Productivity'}
    $market.plugins = @($market.plugins | Where-Object { $_.name -ne 'skillhub-quota-resume' }) + @($entry)
    New-Item -ItemType Directory -Path (Split-Path -Parent $marketPath) -Force | Out-Null
    $marketWritten = $true
    [IO.File]::WriteAllText($marketPath, ($market | ConvertTo-Json -Depth 30), $utf8)
    Write-Output '正在安装 Codex 任务续接 1.0。安装成功后将清理本插件旧版本。'
    $cacheMutationStarted = $true
    & $codexCommand.Source plugin add "$($sourceManifest.name)@$($market.name)" --json
    if ($LASTEXITCODE -ne 0) { throw '更新未完成。若提示拒绝访问，请完全退出客户端后重试；运行中的设置服务可能占用缓存。' }
    $null = Assert-TaskPath -Path $installedCache -Boundary $pluginBase
    foreach ($relative in @('.codex-plugin\plugin.json', '.mcp.json', 'hooks\hooks.json', 'server\settings_mcp.py', 'server\local_catalog.py', 'server\account_binding.py',
            'ui\settings.html', 'ui\launcher.html', 'ui\translations.json', 'ui\manifest-en.json', 'server\i18n.py', 'server\monitor.py', 'skills\quota-resume\scripts\state.py',
            'skills\quota-resume\scripts\hook.py', 'skills\quota-resume\scripts\preferences.py',
            'skills\quota-resume\scripts\recovery_schedule.py', 'skills\quota-resume\SKILL.md',
            'skills\quota-resume\scripts\runtime_paths.py',
            'skills\quota-resume\references\orchestration.md')) {
        $installedFile = Join-Path $installedCache $relative
        $expectedFile = Join-Path $pluginRoot $relative
        if (-not (Test-Path -LiteralPath $installedFile) -or
            (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $expectedFile -Algorithm SHA256).Hash) {
            throw '客户端缓存没有更新为本包的管理界面，未清理旧版。请完全退出客户端后重试；版本号固定，不能仅凭 1.0 判断更新成功。'
        }
    }
} catch {
    $installError = $_
    $cacheRestoreError = $null
    if ($cacheMutationStarted) {
        try {
            Remove-TaskArtifact -Path $installedCache -Boundary $pluginBase
            if ($cacheBackedUp) {
                $null = Assert-TaskPath -Path $cacheBackup -Boundary $pluginBase
                Move-Item -LiteralPath $cacheBackup -Destination $installedCache
                $cacheBackedUp = $false
            }
        } catch { $cacheRestoreError = $_ }
    }
    if ($marketWritten) {
        if ($marketExisted) { [IO.File]::WriteAllBytes($marketPath, $marketBytes) }
        else { Remove-TaskArtifact -Path $marketPath -Boundary (Join-Path $resolvedUserRoot '.agents\plugins') }
    }
    if ($sourceReplaced) { Remove-TaskArtifact -Path $pluginRoot -Boundary $pluginBase }
    if (Test-Path -LiteralPath $backupRoot) {
        $null = Assert-TaskPath -Path $backupRoot -Boundary $pluginBase
        $null = Assert-TaskPath -Path $pluginRoot -Boundary $pluginBase
        Move-Item -LiteralPath $backupRoot -Destination $pluginRoot
    }
    if ($cacheRestoreError) { throw "Installation failed; cache restoration needs attention. Backup retained at $cacheBackup. $($cacheRestoreError.Exception.Message)" }
    throw $installError
} finally {
    if (Test-Path -LiteralPath $stageRoot) { Remove-TaskArtifact -Path $stageRoot -Boundary $pluginBase }
    if ($cacheBackedUp -and -not $cacheMutationStarted) { Remove-TaskArtifact -Path $cacheBackup -Boundary $pluginBase }
}
if ($cacheBackedUp) { Remove-TaskArtifact -Path $cacheBackup -Boundary $pluginBase }
$cacheBase = Join-Path $pluginBase ("cache\$($market.name)\skillhub-quota-resume")
try {
    Clear-OldTaskInstalls -PluginBase $pluginBase -CurrentSource $pluginRoot -CacheBase $cacheBase -Version $sourceManifest.version
} catch { Write-Warning "安装成功，旧副本清理未完成：$($_.Exception.Message)" }
Write-Output '安装成功。重新打开客户端后，可在对话中说：使用 $quota-resume 打开设置弹窗。'
Write-Output '已有设置、监控开关、任务队列和发送记录保留。按客户端提示审核并信任 Hook。'
