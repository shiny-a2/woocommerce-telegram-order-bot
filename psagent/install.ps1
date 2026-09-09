<#
    install.ps1 — نصبِ یک‌بارهٔ ایستگاهِ ادیتِ خودکار روی کامپیوترِ دارای فتوشاپ.

    کاری که می‌کند:
      • ساختِ پوشه‌ها زیرِ ‎-Root‎ (پیش‌فرض C:\psagent): inbox, outbox, sent, work, logs, templates
      • کپیِ BulkComposite-Auto.jsx در work\
      • ساختِ psagent.config.json
      • (اختیاری) ثبتِ Scheduled Task تا خودکار اجرا شود

    نمونه:
      powershell -ExecutionPolicy Bypass -File install.ps1 `
        -HeroTemplate "C:\Users\me\Desktop\hero.psd" `
        -GalleryTemplate "C:\Users\me\Desktop\gallery.psd"

    قالب‌ها را اگر ندهی، از پوشهٔ ‎templates\‎ زیرِ Root حدس می‌زند
    (هر psd ای که «پس زمینه» در نامش باشد = گالری، دیگری = هیرو).
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\psagent",
    [string]$HeroTemplate = "",
    [string]$GalleryTemplate = "",
    [int]$BatchSize = 40,
    [int]$RestartPhotoshopEvery = 10,
    [string]$ManifestUrl = "",
    [string]$DropHost = "",
    [string]$DropUser = "imgdrop",
    [int]$DropPort = 22,
    [switch]$AutoPush,
    [switch]$RegisterTask,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "نصبِ ایستگاهِ ادیتِ خودکار در: $Root" -ForegroundColor Cyan

$dirs = @("inbox", "outbox", "sent", "work", "logs", "templates", ".ssh")
if (-not (Test-Path $Root)) { New-Item -ItemType Directory -Path $Root -Force | Out-Null }
foreach ($d in $dirs) {
    $p = Join-Path $Root $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

# --- اسکریپتِ فوتوشاپ ---
$srcJsx = Join-Path $here "BulkComposite-Auto.jsx"
if (-not (Test-Path $srcJsx)) { throw "BulkComposite-Auto.jsx کنارِ install.ps1 نیست." }
Copy-Item -Path $srcJsx -Destination (Join-Path (Join-Path $Root "work") "BulkComposite-Auto.jsx") -Force
Write-Host "  ✔ اسکریپتِ فوتوشاپ کپی شد"

# --- قالب‌ها ---
$tplDir = Join-Path $Root "templates"
if (-not $HeroTemplate -or -not $GalleryTemplate) {
    $psds = @(Get-ChildItem -Path $tplDir -Filter *.psd -File -ErrorAction SilentlyContinue)
    foreach ($f in $psds) {
        if ($f.Name -match "پس.?زمینه|gallery|background") {
            if (-not $GalleryTemplate) { $GalleryTemplate = $f.FullName }
        } else {
            if (-not $HeroTemplate) { $HeroTemplate = $f.FullName }
        }
    }
}
if (-not $HeroTemplate -or -not $GalleryTemplate) {
    Write-Warning "قالب‌ها مشخص نشدند. دو فایلِ psd را در $tplDir بگذار یا با -HeroTemplate/-GalleryTemplate بده."
    Write-Warning "تنظیمات ساخته می‌شود ولی تا پر نشدنِ قالب‌ها اجرا نمی‌شود."
}

# --- تنظیمات ---
$cfgPath = Join-Path $here "psagent.config.json"
if ((Test-Path $cfgPath) -and -not $Force) {
    Write-Warning "psagent.config.json از قبل هست — دست نخورد (برای بازنویسی -Force بده)."
} else {
    $cfg = [ordered]@{
        root                    = $Root
        hero_template           = $HeroTemplate
        gallery_template        = $GalleryTemplate
        batch_size              = $BatchSize
        restart_photoshop_every = $RestartPhotoshopEvery
        keep_photoshop_open     = $false
        manifest_url            = $ManifestUrl
        auto_push               = [bool]$AutoPush
        drop_host               = $DropHost
        drop_user               = $DropUser
        drop_port               = $DropPort
        drop_key                = (Join-Path (Join-Path $Root ".ssh") "imgdrop_ed25519")
        poll_seconds            = 120
    }
    $json = $cfg | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($cfgPath, $json, (New-Object Text.UTF8Encoding($false)))
    Write-Host "  ✔ تنظیمات نوشته شد: $cfgPath"
}

# --- بررسیِ فتوشاپ ---
try {
    $app = New-Object -ComObject Photoshop.Application
    Write-Host ("  ✔ فتوشاپ در دسترس است: {0}" -f $app.Version)
    try { [Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null } catch { }
} catch {
    Write-Warning "فتوشاپ از راهِ COM جواب نداد. یک‌بار فتوشاپ را دستی باز کن و دوباره امتحان کن."
}

# --- Scheduled Task ---
if ($RegisterTask) {
    $agent = Join-Path $here "psagent.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$agent`" -Watch"
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName "PSAgent-ProductImages" -Action $action -Trigger $trigger `
        -Settings $settings -Description "ادیتِ خودکارِ عکسِ محصول با فتوشاپ" -Force | Out-Null
    Write-Host "  ✔ Scheduled Task ثبت شد (هنگامِ ورود به ویندوز اجرا می‌شود)"
}

Write-Host ""
Write-Host "تمام شد. حالا:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$(Join-Path $here 'psagent.ps1')`" -Preflight"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$(Join-Path $here 'psagent.ps1')`""
