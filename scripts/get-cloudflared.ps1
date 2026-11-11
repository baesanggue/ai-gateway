# 웹 체험판용 터널 프로그램 cloudflared를 받는다 (#90, docs/beta.md). 체험판을 열 PC에서 한 번만.
#
#   powershell -ExecutionPolicy Bypass -File scripts\get-cloudflared.ps1
#
# - Cloudflare 공식 문서가 알려 주는 GitHub 릴리스 주소에서 받는다(약 55MB)
# - Windows 서명이 유효하고 서명자가 Cloudflare, Inc.가 아니면 지우고 멈춘다
# - 받는 곳: %LOCALAPPDATA%\ai-gateway\bin\cloudflared.exe (start.ps1 -Beta가 여기서 찾는다)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"                       # PowerShell 5.1은 진행 막대 때문에 내려받기가 아주 느려진다
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
$Dir = Join-Path $env:LOCALAPPDATA "ai-gateway\bin"
$Exe = Join-Path $Dir "cloudflared.exe"

if (Test-Path $Exe) {
    $sig = Get-AuthenticodeSignature $Exe
    if ($sig.Status -eq "Valid" -and $sig.SignerCertificate.Subject -like 'CN="Cloudflare, Inc."*') {
        Write-Host "이미 있습니다: $Exe (서명 확인)" -ForegroundColor Green
        exit 0
    }
}

Write-Host "cloudflared를 받습니다 (약 55MB): $Url"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$tmp = "$Exe.download"
Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $tmp
$sig = Get-AuthenticodeSignature $tmp
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notlike 'CN="Cloudflare, Inc."*') {
    Remove-Item $tmp -Force
    Write-Host "서명이 Cloudflare, Inc.의 유효한 서명이 아니어서 지웠습니다 ($($sig.Status))." -ForegroundColor Red
    exit 1
}
Move-Item -Force $tmp $Exe
Write-Host "받았습니다: $Exe (서명: Cloudflare, Inc.)" -ForegroundColor Green
Write-Host "다음: powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 → scripts\start.ps1 -Beta"
