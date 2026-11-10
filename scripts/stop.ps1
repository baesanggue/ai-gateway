# AI 게이트웨이 끄기 (#77, Windows): scripts\start.ps1이 켠 것만 끈다 (그 전부터 켜져 있던 Ollaya 등은 그대로 둔다).
#
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#
# PID가 그사이 다른 프로그램에 다시 쓰였을 수 있어(재부팅 등), 적어 둔 시작 시각이 같을 때만 끈다.
# 프로그램 창(Edge/Chrome 앱 창)은 사용자가 닫는다.
$PidFile = Join-Path $env:LOCALAPPDATA "ai-gateway\pids.txt"
if (-not (Test-Path $PidFile)) {
    Write-Host "start.ps1로 켠 프로그램이 없습니다."
    exit 0
}

foreach ($line in Get-Content $PidFile -Encoding UTF8) {
    $name, $id, $started = $line -split " ", 3
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if (-not $p) { Write-Host "  ${name}: 이미 꺼져 있음"; continue }
    if ($p.StartTime.ToString("o") -ne $started) { Write-Host "  ${name}: PID $id 를 다른 프로그램이 쓰고 있어 건드리지 않음"; continue }
    # uv run은 파이썬을 자식 프로세스로 띄운다. 자식까지 함께 끈다(/T)
    taskkill /PID $id /T /F *> $null
    Write-Host "  ${name}: 끔"
}
Remove-Item $PidFile
