# 高迪书法字库预处理工具 - 启动脚本
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = '高迪书法字库预处理工具'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host '========================================'
Write-Host '   高迪书法字库预处理工具'
Write-Host '========================================'
Write-Host ''

# 检查 uv
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host '[错误] 未检测到 uv，请先安装 uv：' -ForegroundColor Red
    Write-Host '  winget install astral-sh.uv'
    Write-Host '  或访问 https://docs.astral.sh/uv/getting-started/installation/'
    Read-Host '按 Enter 退出'
    exit 1
}

# 首次启动自动创建虚拟环境并安装依赖
$VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Host '[初始化] 首次启动，正在使用 uv 创建虚拟环境并安装依赖...' -ForegroundColor Cyan
    Write-Host ''
    & uv sync --python 3.12
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 依赖安装失败，请检查网络或手动运行 uv sync 查看详情' -ForegroundColor Red
        Read-Host '按 Enter 退出'
        exit 1
    }
    Write-Host ''
    Write-Host '[完成] 依赖安装完成' -ForegroundColor Green
    Write-Host ''
}

Write-Host '[启动] 正在启动 Web 服务...' -ForegroundColor Cyan
Write-Host ''
Write-Host '========================================'
Write-Host '  访问地址: http://localhost:7500'
Write-Host '  按 Ctrl+C 停止服务'
Write-Host '========================================'
Write-Host ''

& $VenvPython 'app.py'

# 服务停止后窗口自动关闭（无需再按 Enter）。
# 启动失败（uv 缺失 / 依赖安装失败）仍会停留等用户看完错误后按键。
Write-Host ''
Write-Host '[结束] Web 服务已停止，窗口即将自动关闭...' -ForegroundColor DarkGray
Start-Sleep -Seconds 3
exit 0