# AI 게이트웨이 켜기 (#77, Windows): Ollaya(로컬 판단) → 게이트웨이 → 화면 서버를 창 없이 켜고, 프로그램 창을 연다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-NoWindow] [-Width 1200] [-Height 800]
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Beta     # 설문용 웹 체험판 (#90, docs/beta.md)
#
# - 이미 켜져 있는 것(포트가 열려 있음)은 건드리지 않는다
# - 이 스크립트가 켠 것만 %LOCALAPPDATA%\ai-gateway\pids.txt에 적어 두고, scripts\stop.ps1이 그것만 끈다
# - 실행 기록: %LOCALAPPDATA%\ai-gateway\logs\ (요청 로그와 달리 여기에도 원문은 남지 않는다: 게이트웨이는 가명화된 정보만,
#   Ollaya는 판단할 글을 기록하지 않는다 — docs/ollaya-notes.md §3)
# 먼저 scripts\install.ps1로 설치한다
# -Beta: 화면을 인터넷 주소로 연다(Cloudflare Quick Tunnel). .env에 BETA_PASSWORD가 없으면 열지 않는다. 판단 모델을 체험 기간
#        내내 올려 둔다(OLLAYA_KEEP_ALIVE=24h, 첫 요청 지연을 없앤다, #84). 프로그램 창은 열지 않는다
param([switch]$NoWindow, [int]$Width = 1200, [int]$Height = 800, [switch]$Beta)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$State = Join-Path $env:LOCALAPPDATA "ai-gateway"
$LogDir = Join-Path $State "logs"
$PidFile = Join-Path $State "pids.txt"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
# 포트: Ollaya 기본(docs/ollaya-notes.md §3), 게이트웨이(config.yaml gateway.port), 화면(Streamlit 기본)
$OllayaPort = 11435; $GatewayPort = 8000; $ScreenPort = 8501

function Find-Exe($Name, $Fallback) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($Fallback -and (Test-Path $Fallback)) { return $Fallback }
    Write-Host "$Name 을(를) 찾지 못했습니다. 먼저 scripts\install.ps1을 실행하세요." -ForegroundColor Red
    exit 1
}

function Test-Port([int]$Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect("127.0.0.1", $Port); return $true } catch { return $false } finally { $c.Close() }
}

function Start-Hidden($Name, $Exe, [string[]]$ArgList, [int]$Port, [int]$WaitSeconds) {
    if (Test-Port $Port) { Write-Host "  ${Name}: 이미 켜져 있음 (포트 $Port)"; return }
    $out = Join-Path $LogDir "$Name.log"
    $err = Join-Path $LogDir "$Name.err.log"
    $p = Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    # 이름, PID, 시작 시각: stop.ps1이 PID가 다른 프로그램에 다시 쓰였는지 시작 시각으로 확인한다
    Add-Content -Path $PidFile -Value "$Name $($p.Id) $($p.StartTime.ToString('o'))" -Encoding UTF8
    for ($i = 0; $i -lt $WaitSeconds * 2; $i++) {
        if (Test-Port $Port) { Write-Host "  ${Name}: 켜짐 (포트 $Port)"; return }
        if ($p.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  ${Name}이(가) 켜지지 않았습니다. 기록의 마지막 줄:" -ForegroundColor Red
    if (Test-Path $err) { Get-Content $err -Tail 8 | ForEach-Object { Write-Host "    $_" } }
    Write-Host "  전체 기록: $err" -ForegroundColor Red
    exit 1
}

$uv = Find-Exe "uv" (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
$ollaya = Find-Exe "ollaya" (Join-Path $env:LOCALAPPDATA "Programs\Ollaya\bin\ollaya.exe")

Write-Host "가림게이트 켜기"
if ($Beta) {
    # 비밀번호 없이 인터넷에 열면 설정을 바꿀 수 있는 평소 화면이 열린다. 값은 읽지 않고 있는지만 본다
    $envFile = Join-Path $Root ".env"
    $hasPassword = $env:BETA_PASSWORD -or ((Test-Path $envFile) -and
        (Select-String -Path $envFile -Pattern '^\s*BETA_PASSWORD\s*=\s*\S' -Quiet))
    if (-not $hasPassword) {
        Write-Host ".env에 BETA_PASSWORD가 없어 체험판을 열지 않습니다 (docs/beta.md)." -ForegroundColor Red
        exit 1
    }
    # 터널 프로그램은 서버를 켜기 전에 확인한다 (없으면 서버만 켜지고 멈춘다)
    $cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
    if (-not $cloudflared -and (Test-Path (Join-Path $State "bin\cloudflared.exe"))) { $cloudflared = Join-Path $State "bin\cloudflared.exe" }
    if (-not $cloudflared) {
        Write-Host "터널 프로그램 cloudflared가 없습니다. 먼저 받으세요:" -ForegroundColor Red
        Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\get-cloudflared.ps1"
        exit 1
    }
    if (Test-Port $ScreenPort) {
        Write-Host "화면이 이미 켜져 있어 체험판 설정으로 켜졌는지 알 수 없습니다. stop.ps1로 끄고 다시 실행하세요." -ForegroundColor Red
        exit 1
    }
    if (Test-Port $OllayaPort) {
        Write-Host "  Ollaya가 이미 켜져 있어 모델 유지 설정이 적용되지 않습니다 (첫 요청이 느릴 수 있음)." -ForegroundColor Yellow
    }
    $env:OLLAYA_KEEP_ALIVE = "24h"          # 이 스크립트가 켜는 Ollaya에만 (Ollaya 문서 docs/api.md §6, §15)
    $NoWindow = $true
}
Start-Hidden "ollaya" $ollaya @("serve") $OllayaPort 30
Start-Hidden "gateway" $uv @("run", "--frozen", "uvicorn", "gateway.main:app", "--host", "127.0.0.1", "--port", "$GatewayPort") $GatewayPort 90
# 화면도 이 PC에서만 연다. .streamlit/config.toml에도 있지만 그 파일은 작업 폴더에서만 읽혀서, 명령줄에 한 번 더 적는다 (D20)
Start-Hidden "screen" $uv @("run", "--frozen", "streamlit", "run", "admin/app.py", "--server.address", "127.0.0.1", "--server.port", "$ScreenPort") $ScreenPort 60

try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$GatewayPort/healthz" -TimeoutSec 5
    $dict = if ($h.dictionary -eq "example") { "시연용 가상 사전 (실제 고객명은 가려지지 않음)" } else { "회사 사전" }
    Write-Host "  상태: 보안 수준 $($h.security_level), 업종 $($h.preset), 사용자 사전 $dict"
} catch {
    Write-Host "  게이트웨이 상태를 읽지 못했습니다: $($_.Exception.Message)" -ForegroundColor Yellow
}

if (-not $NoWindow) {
    & (Join-Path $PSScriptRoot "open-app.ps1") -Width $Width -Height $Height -Url "http://localhost:$ScreenPort"
}

if ($Beta) {
    $tlog = Join-Path $LogDir "tunnel.err.log"
    Remove-Item $tlog -ErrorAction SilentlyContinue
    $t = Start-Process -FilePath $cloudflared -ArgumentList @("tunnel", "--url", "http://localhost:$ScreenPort", "--no-autoupdate") `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "tunnel.log") -RedirectStandardError $tlog -PassThru
    Add-Content -Path $PidFile -Value "tunnel $($t.Id) $($t.StartTime.ToString('o'))" -Encoding UTF8
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Path $tlog) {
            $m = Select-String -Path $tlog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value }
        }
    }
    Write-Host ""
    if ($url) {
        Write-Host "체험판 주소: $url" -ForegroundColor Green
        Write-Host "  설문 안내에 이 주소와 비밀번호를 넣으세요. 다시 켜면 주소가 바뀝니다. 체험 기간에는 PC를 켜 두고 절전을 끄세요."
    } else {
        Write-Host "체험판 주소를 받지 못했습니다. 기록: $tlog" -ForegroundColor Red
    }
}
Write-Host ""
Write-Host "기존 서비스의 API 주소를 http://127.0.0.1:$GatewayPort/v1 로 바꾸면 게이트웨이를 거칩니다." -ForegroundColor Green
Write-Host "끄기: powershell -ExecutionPolicy Bypass -File scripts\stop.ps1   기록: $LogDir"
