# 발표 5장의 화면 사진을 시연 영상으로 바꾼다 (#106). build_deck.py로 발표 파일을 다시 만들면 이 스크립트도 다시 실행한다.
#
#   powershell -ExecutionPolicy Bypass -File docs\presentation\add_video.ps1 [-Video docs\presentation\demo-30s.mp4]
#
# - PowerPoint(COM)로 연다. python-pptx는 영상을 넣을 수는 있지만 "슬라이드가 열리면 자동 재생"을 정할 수 없다
# - 사진과 같은 자리·너비에 넣고(높이는 영상 비율), 자동 재생, 표지 그림은 가린 글·복원 장면(17초)
# - 녹화 영역이 프로그램 창보다 넓어 둘레가 하얗다. 창 안쪽만 보이게 자른다(-CropPx: 원본 1436x980 기준 왼쪽·위·오른쪽·아래 픽셀,
#   11/13 프레임에서 창 테두리 위치를 재서 정함). 글자가 약 20% 커진다
# - 영상은 발표 파일 안에 들어간다(링크 아님). 발표 PC에 영상 파일을 따로 가져갈 필요가 없다
param([string]$Video = "", [string]$Deck = "", [int]$PosterMs = 17000, [string]$CropPx = "101,87,151,102",
      [int]$VideoW = 1436, [int]$VideoH = 980)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
if (-not $Video) { $Video = Join-Path $Here "demo-30s.mp4" }
if (-not $Deck) { $Deck = Join-Path $Here "2026-11-17-garimgate.pptx" }
$Video = (Resolve-Path $Video).Path
$Deck = (Resolve-Path $Deck).Path
$PicName = "시연 화면 (녹화 영상으로 바꾸기)"
$VideoName = "시연 영상 (30초, 자동 재생)"

$pp = New-Object -ComObject PowerPoint.Application
try {
    $pres = $pp.Presentations.Open($Deck, 0, 0, 0)          # 읽기 전용 아님, 창 없이
    $slide = $pres.Slides(5)
    $old = $null
    foreach ($sh in $slide.Shapes) { if ($sh.Name -eq $PicName -or $sh.Name -eq $VideoName) { $old = $sh } }
    if (-not $old) { throw "5장에서 '$PicName'을 찾지 못했습니다. build_deck.py로 다시 만든 파일인지 확인하세요." }
    $left = $old.Left; $top = $old.Top; $width = $old.Width
    $old.Delete()

    $v = $slide.Shapes.AddMediaObject2($Video, 0, -1, $left, $top, $width)   # 링크 안 함, 파일 안에 저장
    if ($CropPx) {
        $l, $t, $r, $b = $CropPx.Split(',') | ForEach-Object { [double]$_ }
        $v.LockAspectRatio = -1
        $v.ScaleHeight(1, -1); $v.ScaleWidth(1, -1)          # 원래 크기로 (자르기 값은 원래 크기 기준 포인트)
        $ow = $v.Width; $oh = $v.Height
        $v.PictureFormat.CropLeft = $ow * $l / $VideoW
        $v.PictureFormat.CropTop = $oh * $t / $VideoH
        $v.PictureFormat.CropRight = $ow * $r / $VideoW
        $v.PictureFormat.CropBottom = $oh * $b / $VideoH
    }
    $v.LockAspectRatio = -1
    $v.Width = $width
    $v.Left = $left; $v.Top = $top
    $v.Name = $VideoName
    $v.Line.Visible = -1
    $v.Line.ForeColor.RGB = 0xE5E7EB                        # build_deck.py의 LINE (BGR 순서라 회색은 같다)
    $v.Line.Weight = 1
    $v.AnimationSettings.PlaySettings.PlayOnEntry = -1      # 슬라이드가 열리면 바로 재생
    $v.AnimationSettings.PlaySettings.HideWhileNotPlaying = 0
    $v.MediaFormat.SetDisplayPicture($PosterMs)             # 인쇄·PDF·재생 전 표지 그림
    $pres.Save()
    "5장에 영상을 넣었습니다: {0:N1}초, 위치 ({1:N0}, {2:N0}) 크기 {3:N0}x{4:N0}pt" -f ($v.MediaFormat.Length / 1000), $v.Left, $v.Top, $v.Width, $v.Height
    $pres.Close()
} finally {
    $pp.Quit()
}
