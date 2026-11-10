# AI 게이트웨이 설치 (#77, Windows). 새로 받은 폴더에서 한 번 실행한다. 다시 실행해도 된다(있는 것은 건너뛴다).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-Yes]
#
# 하는 일 (밖에서 받는 것은 시작 전에 목록을 보여 주고 확인받는다. -Yes면 묻지 않는다)
#   1. uv(파이썬 패키지 관리자)가 없으면 공식 설치 스크립트로 설치
#   2. 파이썬 패키지 설치: uv sync --frozen (uv.lock에 고정한 버전 그대로, 파이썬 3.11+가 없으면 uv가 받는다)
#   3. Ollaya(로컬 판단 서버)가 없으면 공식 설치 스크립트로 CPU용만 설치 (GPU 팩 1.4GB는 받지 않는다, 가벼움 원칙)
#   4. 판단 모델 gliclass:v4-edge를 측정한 버전(digest)으로 고정해 받기 (D25)
#   5. 이름 인식 모델을 고정 커밋에서 받아 SHA-256 확인 (D23, docs/ner-model.md). 다르면 지우고 멈춘다
#   6. .env가 없으면 .env.example로 만들고 OpenAI API 키를 받는다 (입력은 화면에 보이지 않고, 어디에도 출력하지 않는다)
#   7. 사용자 사전이 없으면 시연용 가상 사전을 복사한다 (D21). 실제 고객명이 가려지지 않으므로 크게 알린다
#   8. 가명화 부품을 한 번 만들어 사전·이름 인식 모델이 제대로 읽히는지 확인
# 실행은 scripts\start.ps1, 끄기는 scripts\stop.ps1
param([switch]$Yes)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"                       # PowerShell 5.1은 진행 막대 때문에 내려받기가 아주 느려진다
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# ── 고정한 버전 (바꿀 때는 docs/ner-model.md, gateway/masking/ner.py의 PINNED, docs/install.md를 함께 고친다) ──
$NerRepo = "atonlee/koelectra-ko-pii-ner"
$NerCommit = "1e75c01e707232401883cf364151bbe2e560c708"
$NerDir = Join-Path $Root "models\koelectra-ko-pii-ner"
$NerFiles = [ordered]@{
    "onnx/model_int8.onnx" = "53cd2da73867d0a66ea2bec70b582d164115803788438bd63fd8a9ed365868e7"
    "config.json"          = "65b1c2e17c4eb6d4f5be29e4374784a03c7b886229af948757e55f3b79444a23"
    "tokenizer.json"       = "9bc85a2412ae3694bffac69cb152a9aafd59fd0357e540a343fa9daa62099c38"
}
$JudgeModel = "gliclass:v4-edge"
$JudgeDigest = "sha256:3b1849b2b5ec38109cbf8772715bccaf8f546ab48f6ecefdee41f4d6af1c5b39"   # 11/10 이 PC에서 ollaya show로 확인
$OllayaDefault = Join-Path $env:LOCALAPPDATA "Programs\Ollaya\bin\ollaya.exe"            # 공식 설치 위치 (docs/ollaya-notes.md §2)
$OllayaPort = 11435

function Say($text) { Write-Host $text }
function Step($n, $text) { Write-Host ""; Write-Host "[$n/8] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "설치를 멈췄습니다: $text" -ForegroundColor Red; exit 1 }

# 외부 명령: PowerShell 5.1은 stderr 출력을 오류로 바꿀 수 있어, 실행하는 동안만 Continue로 두고 종료 코드로 판단한다
function Invoke-Native([scriptblock]$Block, [string]$What) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0) { Fail "$What (종료 코드 $LASTEXITCODE)" }
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $local = Join-Path $env:USERPROFILE ".local\bin\uv.exe"           # 공식 설치 스크립트의 기본 위치
    if (Test-Path $local) { return $local }
    return $null
}

function Find-Ollaya {
    $cmd = Get-Command ollaya -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if (Test-Path $OllayaDefault) { return $OllayaDefault }
    return $null
}

function Test-Port([int]$Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect("127.0.0.1", $Port); return $true } catch { return $false } finally { $c.Close() }
}

function Get-Sha256($Path) { return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower() }

function Test-NerFile($Rel) {
    $path = Join-Path $NerDir $Rel
    return (Test-Path $path) -and ((Get-Sha256 $path) -eq $NerFiles[$Rel])
}

function Get-JudgeDigest($Ollaya) {
    # ollaya show의 digest 줄 (없거나 서버가 꺼져 있으면 빈 문자열)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $lines = & $Ollaya show $JudgeModel 2>$null } finally { $ErrorActionPreference = $old }
    foreach ($line in $lines) {
        if ($line -match "^\s*digest\s+(sha256:[0-9a-f]{64})") { return $Matches[1] }
    }
    return ""
}

# ── 무엇이 필요한지 먼저 본다 ──
$uv = Find-Uv
$ollaya = Find-Ollaya
$startedOllaya = $null
if ($ollaya -and -not (Test-Port $OllayaPort)) {
    # 모델이 있는지 보려면 서버가 떠 있어야 한다. 이 스크립트가 켠 것은 끝날 때 끈다
    $startedOllaya = Start-Process -FilePath $ollaya -ArgumentList "serve" -WindowStyle Hidden -PassThru
    for ($i = 0; $i -lt 40 -and -not (Test-Port $OllayaPort); $i++) { Start-Sleep -Milliseconds 500 }
}
$needJudge = (-not $ollaya) -or ((Get-JudgeDigest $ollaya) -ne $JudgeDigest)
$needNer = @($NerFiles.Keys | Where-Object { -not (Test-NerFile $_) })
$needEnv = -not (Test-Path (Join-Path $Root ".env"))

Say "AI 게이트웨이 설치 — $Root"
Say ""
Say "밖에서 받는 것:"
if (-not $uv) { Say "  - uv (파이썬 패키지 관리자): astral.sh 공식 설치 스크립트" }
Say "  - 파이썬 패키지: uv.lock에 고정한 버전 (이미 있으면 건너뜀)"
if (-not $ollaya) { Say "  - Ollaya (로컬 판단 서버) CPU용 약 50MB: ollaya.dev 공식 설치 스크립트 (sha256 확인)" }
if ($needJudge) { Say "  - 판단 모델 $JudgeModel 약 895MB: Ollaya 공식 저장소, 측정한 버전으로 고정" }
if ($needNer.Count -gt 0) { Say "  - 이름 인식 모델 약 15.5MB: huggingface.co/$NerRepo (커밋·해시 고정)" }
if ($needEnv) { Say "만드는 것: .env (OpenAI API 키를 입력받습니다)" }
Say ""
if (-not $Yes) {
    $answer = Read-Host "계속할까요? (Y/N)"
    if ($answer -notmatch "^[Yy]") {
        if ($startedOllaya) { Stop-Process -Id $startedOllaya.Id -Force -ErrorAction SilentlyContinue }
        Say "설치하지 않았습니다."
        exit 0
    }
}

try {
    # 1. uv
    Step 1 "uv"
    if ($uv) {
        Say "  있음: $uv"
    } else {
        Invoke-Native { powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" } "uv 설치"
        $uv = Find-Uv
        if (-not $uv) { Fail "uv를 설치했지만 찾지 못했습니다. 새 PowerShell 창에서 다시 실행하세요." }
        Say "  설치함: $uv"
    }

    # 2. 파이썬 패키지
    Step 2 "파이썬 패키지 (uv sync --frozen)"
    Invoke-Native { & $uv sync --frozen } "uv sync"

    # 3. Ollaya
    Step 3 "Ollaya (로컬 판단 서버)"
    if ($ollaya) {
        Say "  있음: $ollaya"
    } else {
        $env:OLLAYA_NO_CUDA = "1"                                 # CPU용만 (docs/ollaya-notes.md §2)
        Invoke-Native { powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://ollaya.dev/install.ps1 | iex" } "Ollaya 설치"
        $ollaya = Find-Ollaya
        if (-not $ollaya) { Fail "Ollaya를 설치했지만 찾지 못했습니다 ($OllayaDefault)." }
        Say "  설치함: $ollaya"
    }

    # 4. 판단 모델 (버전 고정)
    Step 4 "판단 모델 $JudgeModel"
    if (-not (Test-Port $OllayaPort)) {
        $startedOllaya = Start-Process -FilePath $ollaya -ArgumentList "serve" -WindowStyle Hidden -PassThru
        for ($i = 0; $i -lt 40 -and -not (Test-Port $OllayaPort); $i++) { Start-Sleep -Milliseconds 500 }
        if (-not (Test-Port $OllayaPort)) { Fail "Ollaya 서버가 켜지지 않았습니다 (포트 $OllayaPort)." }
    }
    if ((Get-JudgeDigest $ollaya) -eq $JudgeDigest) {
        Say "  있음: 측정한 버전 ($($JudgeDigest.Substring(0, 19))...)"
    } else {
        # model@digest: 그 버전의 목록(manifest)을 digest로 받아 확인한 뒤 같은 이름으로 저장한다 (ollaya pull --help)
        Invoke-Native { & $ollaya pull "$JudgeModel@$JudgeDigest" } "판단 모델 받기"
        if ((Get-JudgeDigest $ollaya) -ne $JudgeDigest) { Fail "판단 모델의 버전이 측정한 버전과 다릅니다." }
        Say "  받음: 측정한 버전"
    }

    # 5. 이름 인식 모델 (커밋·해시 고정)
    Step 5 "이름 인식 모델 ($NerRepo)"
    foreach ($rel in $NerFiles.Keys) {
        $path = Join-Path $NerDir $rel
        if (Test-NerFile $rel) { Say "  있음: $rel"; continue }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
        $tmp = "$path.download"
        Invoke-WebRequest -UseBasicParsing -Uri "https://huggingface.co/$NerRepo/resolve/$NerCommit/$rel" -OutFile $tmp
        if ((Get-Sha256 $tmp) -ne $NerFiles[$rel]) {
            Remove-Item $tmp -Force
            Fail "$rel 의 SHA-256이 고정한 값과 다릅니다. 받은 파일을 지웠습니다."
        }
        Move-Item -Force $tmp $path
        Say "  받음: $rel (해시 확인)"
    }

    # 6. .env (OpenAI API 키)
    Step 6 ".env (비밀값)"
    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root ".env.example") $envPath; Say "  만듦: .env.example에서" }
    $lines = [IO.File]::ReadAllLines($envPath)
    $hasKey = @($lines | Where-Object { $_ -match "^\s*OPENAI_API_KEY\s*=\s*\S" }).Count -gt 0   # "= 키"처럼 띄어 써도 설정 로더는 읽는다
    if ($hasKey) {
        Say "  OpenAI API 키: 있음"
    } elseif ($Yes) {
        Say "  OpenAI API 키: 비어 있음. .env의 OPENAI_API_KEY= 뒤에 직접 넣으세요 (판단·답변 생성에 필요)"
    } else {
        $secure = Read-Host "  OpenAI API 키 (입력은 화면에 보이지 않습니다. 비우고 Enter면 건너뜀)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if ($key) {
            $out = foreach ($line in $lines) { if ($line -match "^\s*OPENAI_API_KEY\s*=") { "OPENAI_API_KEY=$key" } else { $line } }
            [IO.File]::WriteAllText($envPath, (($out -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
            $key = $null
            Say "  OpenAI API 키: 저장함 (.env, 커밋되지 않음)"
        } else {
            Say "  OpenAI API 키: 건너뜀. 나중에 .env의 OPENAI_API_KEY= 뒤에 넣으세요"
        }
    }

    # 7. 사용자 사전
    Step 7 "사용자 사전"
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $paths = & $uv run --frozen python -c "from gateway.config import load_settings; m = load_settings().config.masking; print(m.user_dictionary_path); print(m.example_dictionary_path)" 2>$null
    } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0 -or @($paths).Count -lt 2) { Fail "설정(config.yaml)을 읽지 못했습니다." }
    $dict = Join-Path $Root $paths[0]
    $example = Join-Path $Root $paths[1]
    if (Test-Path $dict) {
        Say "  있음: $($paths[0]) (내용은 출력하지 않습니다)"
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dict) | Out-Null
        Copy-Item $example $dict
        Write-Host "  복사함: 시연용 가상 사전 → $($paths[0])" -ForegroundColor Yellow
        Write-Host "  !! 시연용 가상 이름만 들어 있습니다. 실제 고객명은 가려지지 않습니다." -ForegroundColor Yellow
        Write-Host "  !! 운영 전에 프로그램의 [설정 > 사용자 사전]에서 회사의 고객명·직원명으로 바꾸세요." -ForegroundColor Yellow
    }

    # 8. 확인: 가명화 부품을 한 번 만든다 (사전 형식·이름 인식 모델 해시를 게이트웨이와 같은 코드로 확인)
    Step 8 "확인"
    Invoke-Native { & $uv run --frozen python -c "from gateway.config import load_settings; from gateway.masking.masker import RuleMasker; RuleMasker.from_config(load_settings().config.masking); print('  가명화 부품: 정상')" } "가명화 부품 확인"
}
finally {
    if ($startedOllaya) { Stop-Process -Id $startedOllaya.Id -Force -ErrorAction SilentlyContinue }   # 이 스크립트가 켠 서버만 끈다
}

Say ""
Write-Host "설치를 마쳤습니다. 실행: powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Green
