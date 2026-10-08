$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw '未找到 .venv。请先按 BUILD.md 建立虚拟环境并安装构建依赖。'
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot 'Shike.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出码：$LASTEXITCODE"
    }
    Write-Host "构建完成：$(Join-Path $projectRoot 'dist\Shike.exe')"
} finally {
    Pop-Location
}
