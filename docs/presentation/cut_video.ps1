# 시연 녹화에서 구간만 잘라 이어 붙인다 (#106). Windows 기본 영상 편집 API(WinRT Windows.Media.Editing)라 내려받을 것이 없다.
#
#   powershell -ExecutionPolicy Bypass -File docs\presentation\cut_video.ps1 -Video <원본.mp4> -OutPath <결과.mp4> -Segments "25.0-27.5,30.5-36.0"
#
# - 구간은 초 단위. 소리는 넣지 않는다(발표자가 말한다). 크기는 원본과 같은 1436x980, 30fps
# - 11/13 demo-30s.mp4에 쓴 구간 (원본 2분 1초, 프레임을 1초·0.5초마다 뽑아 장면 경계를 찾았다):
#     25.0-27.5   시연 메뉴 클릭
#     30.5-36.0   배송 문의 → 정해 둔 답
#     64.5-76.0   사과 메일 붙여 넣기 → 처리 중 → 가린 글·복원된 답
#     97.0-102.5  주민번호 → 막고 안내
#     110.0-114.5 비용 카드
# - PowerShell 5.1은 WinRT 비동기·컬렉션을 바로 못 다뤄서 AsTask와 ICollection.Add를 리플렉션으로 부른다
param([string]$Video, [string]$OutPath, [string]$Segments, [int]$Bitrate = 3000000)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTask1 = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTask2 = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperationWithProgress`2' })[0]
function Await($op, [Type]$t) { $task = $asTask1.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait() | Out-Null; $task.Result }
function AwaitP($op, [Type]$t, [Type]$p) { $task = $asTask2.MakeGenericMethod($t, $p).Invoke($null, @($op)); $task.Wait() | Out-Null; $task.Result }
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFolder, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Editing.MediaComposition, Windows.Media.Editing, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Editing.MediaClip, Windows.Media.Editing, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Transcoding.TranscodeFailureReason, Windows.Media.Transcoding, ContentType = WindowsRuntime] | Out-Null

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Video)) ([Windows.Storage.StorageFile])
$comp = New-Object Windows.Media.Editing.MediaComposition
$add = [System.Collections.Generic.ICollection[Windows.Media.Editing.MediaClip]].GetMethod('Add')
$total = 0.0
foreach ($seg in $Segments.Split(',')) {
    $a, $b = $seg.Split('-') | ForEach-Object { [double]$_ }
    $clip = Await ([Windows.Media.Editing.MediaClip]::CreateFromFileAsync($file)) ([Windows.Media.Editing.MediaClip])
    $len = $clip.OriginalDuration.TotalSeconds
    $clip.TrimTimeFromStart = [TimeSpan]::FromSeconds($a)
    $clip.TrimTimeFromEnd = [TimeSpan]::FromSeconds([Math]::Max(0, $len - $b))
    $add.Invoke($comp.Clips, @($clip)) | Out-Null
    $total += ($b - $a)
}
"segments total = $total s, composition = $($comp.Duration)"

$profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality]::HD1080p)
$profile.Video.Width = 1436
$profile.Video.Height = 980
$profile.Video.Bitrate = $Bitrate
$profile.Video.FrameRate.Numerator = 30
$profile.Video.FrameRate.Denominator = 1
$profile.Audio = $null

$dir = Split-Path -Parent $OutPath
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync($dir)) ([Windows.Storage.StorageFolder])
$out = Await ($folder.CreateFileAsync((Split-Path -Leaf $OutPath), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
$reason = AwaitP ($comp.RenderToFileAsync($out, [Windows.Media.Editing.MediaTrimmingPreference]::Precise, $profile)) ([Windows.Media.Transcoding.TranscodeFailureReason]) ([double])
"render result = $reason"
