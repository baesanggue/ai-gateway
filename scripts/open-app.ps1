# AI 게이트웨이 화면을 프로그램 창으로 연다 (#76, 11/09 사용자 의견: 창은 일에 방해되지 않는 크기로).
# Edge(없으면 Chrome)의 앱 창(주소창 없음)으로, 크기는 1200×800 안팎. 화면 서버(streamlit)가 먼저 떠 있어야 한다.
#   powershell -ExecutionPolicy Bypass -File scripts\open-app.ps1 [-Width 1200] [-Height 800]
param(
    [int]$Width = 1200,
    [int]$Height = 800,
    [string]$Url = "http://localhost:8501"
)

$candidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$browser = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $browser) {
    Write-Host "Edge나 Chrome을 찾지 못했습니다. 브라우저에서 $Url 을 여세요."
    exit 1
}

# 창 크기를 지키려고 이 화면 전용 프로필을 쓴다 (평소 브라우저 창·탭과 섞이지 않는다)
$profileDir = Join-Path $env:LOCALAPPDATA "ai-gateway\app-window"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
Start-Process -FilePath $browser -ArgumentList @(
    "--app=$Url",
    "--window-size=$Width,$Height",
    "--user-data-dir=$profileDir",
    "--no-first-run",
    "--no-default-browser-check"
)
