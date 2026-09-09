<#
    psagent.ps1 — اجراکنندهٔ خودکارِ ادیتِ عکسِ محصول روی ایستگاهِ طراحی.

    این اسکریپت روی کامپیوتری اجرا می‌شود که فتوشاپ دارد. کارش:
      ۱) (اختیاری) خام‌های تازه را از روی مانیفستِ سرور در inbox دانلود می‌کند.
      ۲) فتوشاپ را از راهِ COM صدا می‌زند و BulkComposite-Auto.jsx را بچ‌بچ اجرا می‌کند
         (inbox → outbox) — بدونِ هیچ دیالوگ و بدونِ دخالتِ کاربر.
      ۳) (اختیاری) خروجی‌ها را با یک کلیدِ SSHِ محدود به سرور می‌فرستد.

    هیچ چیزی روی سایت نمی‌نویسد. آپلود به کتابخانهٔ رسانه و درجِ سئو سمتِ سرور انجام می‌شود.

    اجرا:
      powershell -ExecutionPolicy Bypass -File psagent.ps1            # یک دور
      powershell -ExecutionPolicy Bypass -File psagent.ps1 -Watch     # حلقه (سرویس‌گونه)
      powershell -ExecutionPolicy Bypass -File psagent.ps1 -Status    # فقط وضعیت
      powershell -ExecutionPolicy Bypass -File psagent.ps1 -Preflight # فقط بررسیِ آمادگی

    سازگار با Windows PowerShell 5.1 (بدون ‎&&‎ / ‎??‎ / ternary).
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = "",
    [switch]$Watch,
    [switch]$Status,
    [switch]$Preflight,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$script:Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- config ----
function Get-AgentConfig {
    param([string]$Path)
    if (-not $Path) { $Path = Join-Path $script:Here "psagent.config.json" }
    if (-not (Test-Path $Path)) {
        throw "فایلِ تنظیمات پیدا نشد: $Path  (اول install.ps1 را اجرا کن)"
    }
    $cfg = Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($k in @("root", "hero_template", "gallery_template")) {
        if (-not $cfg.$k) { throw "کلیدِ '$k' در تنظیمات خالی است." }
    }
    return $cfg
}

function Get-Paths {
    param($Cfg)
    $root = $Cfg.root
    return [pscustomobject]@{
        Root    = $root
        Inbox   = Join-Path $root "inbox"
        Outbox  = Join-Path $root "outbox"
        Sent    = Join-Path $root "sent"
        Work    = Join-Path $root "work"
        Logs    = Join-Path $root "logs"
        Jsx     = Join-Path (Join-Path $root "work") "BulkComposite-Auto.jsx"
        JobIni  = Join-Path (Join-Path $root "work") "job.ini"
        Result  = Join-Path (Join-Path $root "work") "result.tsv"
        Log     = Join-Path (Join-Path $root "logs") "psagent.log"
        Failed  = Join-Path (Join-Path $root "logs") "failed.tsv"
    }
}

function New-Folders {
    param($P)
    foreach ($d in @($P.Root, $P.Inbox, $P.Outbox, $P.Sent, $P.Work, $P.Logs)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
}

# ------------------------------------------------------------------ log ----
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    if ($script:P) {
        try { Add-Content -Path $script:P.Log -Value $line -Encoding UTF8 } catch { }
    }
}

# ------------------------------------------------------------ preflight ----
function Test-Preflight {
    param($Cfg, $P)
    $problems = @()

    if (-not (Test-Path $Cfg.hero_template))    { $problems += "قالبِ هیرو پیدا نشد: $($Cfg.hero_template)" }
    if (-not (Test-Path $Cfg.gallery_template)) { $problems += "قالبِ گالری پیدا نشد: $($Cfg.gallery_template)" }
    if (-not (Test-Path $P.Jsx))                { $problems += "اسکریپتِ فوتوشاپ پیدا نشد: $($P.Jsx)" }

    $psOk = $false
    try { $null = New-Object -ComObject Photoshop.Application; $psOk = $true } catch { $psOk = $false }
    if (-not $psOk) { $problems += "فتوشاپ از راهِ COM در دسترس نیست (نصب/لایسنس/نسخه را چک کن)." }

    if ($Cfg.auto_push -and -not $NoPush) {
        if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { $problems += "tar.exe پیدا نشد (برای ارسال لازم است)." }
        if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { $problems += "ssh.exe پیدا نشد (OpenSSH Client را نصب کن)." }
        if (-not $Cfg.drop_host) { $problems += "drop_host خالی است ولی auto_push روشن است." }
        if ($Cfg.drop_key -and -not (Test-Path $Cfg.drop_key)) { $problems += "کلیدِ ارسال پیدا نشد: $($Cfg.drop_key)" }
    }

    return $problems
}

# ------------------------------------------------------------- inventory ----
function Get-Inventory {
    param($P)
    $inbox  = @(Get-ChildItem -Path $P.Inbox  -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|webp)$' -and $_.Name -notmatch '^(?i)logo\.' })
    $outbox = @(Get-ChildItem -Path $P.Outbox -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -match '^\.(jpg|jpeg)$' })
    $sent   = @(Get-ChildItem -Path $P.Sent   -File -ErrorAction SilentlyContinue)
    # هر خامِ «پایه» دو خروجی می‌سازد (هیرو + گالری‌۱)؛ هر خامِ پسونددار یک خروجی.
    $stems = @{}
    foreach ($f in $inbox) { $stems[[IO.Path]::GetFileNameWithoutExtension($f.Name)] = $true }
    $expected = 0
    foreach ($f in $inbox) {
        $stem = [IO.Path]::GetFileNameWithoutExtension($f.Name)
        $m = [regex]::Match($stem, '^(.+)-(\d+)$')
        if ($m.Success -and $stems.ContainsKey($m.Groups[1].Value)) { $expected += 1 } else { $expected += 2 }
    }
    return [pscustomobject]@{
        InboxCount    = $inbox.Count
        OutboxCount   = $outbox.Count
        SentCount     = $sent.Count
        ExpectedTotal = $expected
    }
}

# ------------------------------------------------------------- photoshop ----
function Get-Photoshop {
    try { return [Runtime.InteropServices.Marshal]::GetActiveObject("Photoshop.Application") }
    catch { return New-Object -ComObject Photoshop.Application }
}

function Close-Photoshop {
    param($App)
    if (-not $App) { return }
    try { $App.Quit() } catch { }
    try { [Runtime.InteropServices.Marshal]::ReleaseComObject($App) | Out-Null } catch { }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    Start-Sleep -Seconds 3
}

function Write-JobIni {
    param($Cfg, $P)
    $lines = @(
        "hero_template=$($Cfg.hero_template)",
        "gallery_template=$($Cfg.gallery_template)",
        "input=$($P.Inbox)",
        "output=$($P.Outbox)",
        "max_jobs=$($Cfg.batch_size)"
    )
    # ExtendScript فایل را UTF-8 می‌خواند؛ BOM نگذاریم.
    [IO.File]::WriteAllText($P.JobIni, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding($false)))
}

function Invoke-PhotoshopBatch {
    param($App, $P)
    if (Test-Path $P.Result) { Remove-Item $P.Result -Force -ErrorAction SilentlyContinue }
    $out = $null
    for ($try = 1; $try -le 3; $try++) {
        try {
            $out = $App.DoJavaScriptFile($P.Jsx, $null, 3)
            break
        } catch {
            # «call was rejected by callee» یعنی فتوشاپ لحظه‌ای مشغول است — دوباره تلاش کن.
            Write-Log ("تلاشِ {0} برای صدا زدنِ فتوشاپ نشد: {1}" -f $try, $_.Exception.Message) "WARN"
            Start-Sleep -Seconds (5 * $try)
        }
    }
    if ($null -eq $out) { throw "فتوشاپ به سه تلاش جواب نداد." }
    return Read-BatchResult -P $P -Returned ([string]$out)
}

function Read-BatchResult {
    param($P, [string]$Returned)
    $ok = 0; $skip = 0; $fail = 0; $remaining = -1
    $failures = @()

    if (Test-Path $P.Result) {
        foreach ($line in (Get-Content -Path $P.Result -Encoding UTF8)) {
            $parts = $line -split "`t"
            switch ($parts[0]) {
                "STATS" {
                    if ($parts.Count -ge 5) {
                        $ok = [int]$parts[1]; $skip = [int]$parts[2]
                        $fail = [int]$parts[3]; $remaining = [int]$parts[4]
                    }
                }
                "FAIL" { $failures += ,$parts }
            }
        }
    }
    if ($remaining -lt 0 -and $Returned -like "STATS*") {
        $parts = $Returned -split "`t"
        if ($parts.Count -ge 5) {
            $ok = [int]$parts[1]; $skip = [int]$parts[2]
            $fail = [int]$parts[3]; $remaining = [int]$parts[4]
        }
    }
    if ($Returned -like "FATAL*") { throw "اسکریپتِ فوتوشاپ خطای مهلک داد: $Returned" }

    foreach ($f in $failures) {
        $reason = ""
        if ($f.Count -ge 3) { $reason = $f[2] }
        Add-Content -Path $P.Failed -Value ("{0}`t{1}`t{2}" -f (Get-Date -Format s), $f[1], $reason) -Encoding UTF8
    }

    return [pscustomobject]@{ Ok = $ok; Skipped = $skip; Failed = $fail; Remaining = $remaining }
}

function Invoke-Compositing {
    param($Cfg, $P)
    Write-JobIni -Cfg $Cfg -P $P
    $app = Get-Photoshop
    $batches = 0
    $totalOk = 0; $totalFail = 0
    try {
        while ($true) {
            $r = Invoke-PhotoshopBatch -App $app -P $P
            $batches++
            $totalOk += $r.Ok; $totalFail += $r.Failed
            Write-Log ("بچ {0}: ساخته‌شده={1} ردشده={2} خطا={3} باقی‌مانده={4}" -f `
                       $batches, $r.Ok, $r.Skipped, $r.Failed, $r.Remaining)

            if ($r.Remaining -le 0) { break }
            if ($r.Ok -eq 0 -and $r.Failed -eq 0) {
                Write-Log "بچ هیچ کاری جلو نبرد — برای جلوگیری از حلقهٔ بی‌پایان متوقف می‌شوم." "WARN"
                break
            }
            $every = [int]$Cfg.restart_photoshop_every
            if ($every -gt 0 -and ($batches % $every) -eq 0) {
                Write-Log "تازه‌سازیِ فتوشاپ (ضدِ تورمِ حافظه)…"
                Close-Photoshop -App $app
                $app = Get-Photoshop
            }
        }
    } finally {
        if ($Cfg.keep_photoshop_open) { } else { Close-Photoshop -App $app }
    }
    return [pscustomobject]@{ Ok = $totalOk; Failed = $totalFail; Batches = $batches }
}

# ------------------------------------------------------------------ pull ----
function Invoke-Pull {
    param($Cfg, $P)
    if (-not $Cfg.manifest_url) { return 0 }
    Write-Log "خواندنِ مانیفستِ خام‌ها…"
    try {
        $manifest = Invoke-RestMethod -Uri $Cfg.manifest_url -TimeoutSec 60
    } catch {
        Write-Log ("مانیفست خوانده نشد: {0}" -f $_.Exception.Message) "WARN"
        return 0
    }
    $n = 0
    foreach ($item in $manifest.files) {
        $name = [IO.Path]::GetFileName([string]$item.name)
        if (-not $name -or $name -match '[\\/:*?"<>|]') { continue }
        $dest = Join-Path $P.Inbox $name
        if (Test-Path $dest) { continue }
        if (Test-Path (Join-Path $P.Sent $name)) { continue }
        try {
            Invoke-WebRequest -Uri $item.url -OutFile $dest -TimeoutSec 120 -UseBasicParsing
            if ((Get-Item $dest).Length -lt 2000) { Remove-Item $dest -Force; continue }
            $n++
        } catch {
            Write-Log ("دانلودِ {0} نشد" -f $name) "WARN"
            if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
        }
    }
    Write-Log "$n خامِ تازه دانلود شد."
    return $n
}

# ------------------------------------------------------------------ push ----
function Invoke-Push {
    param($Cfg, $P)
    $files = @(Get-ChildItem -Path $P.Outbox -File -ErrorAction SilentlyContinue |
               Where-Object { $_.Extension -match '^\.(jpg|jpeg)$' })
    if ($files.Count -eq 0) { Write-Log "چیزی برای ارسال نیست."; return 0 }

    $stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
    $tarPath = Join-Path $P.Work ("drop-$stamp.tar.gz")
    $listPath = Join-Path $P.Work ("drop-$stamp.txt")
    ($files | ForEach-Object { $_.Name }) | Set-Content -Path $listPath -Encoding UTF8

    # tar را روی فایل می‌سازیم؛ لولهٔ پاورشل باینری را خراب می‌کند.
    & tar.exe -czf $tarPath -C $P.Outbox -T $listPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tarPath)) { throw "ساختِ بستهٔ ارسال نشد." }

    $sshArgs = @("-i", "`"$($Cfg.drop_key)`"", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=20", "$($Cfg.drop_user)@$($Cfg.drop_host)")
    if ($Cfg.drop_port) { $sshArgs = @("-p", [string]$Cfg.drop_port) + $sshArgs }
    $cmdLine = "ssh.exe " + ($sshArgs -join " ") + " < `"$tarPath`""
    Write-Log ("ارسالِ {0} فایل ({1:N1} مگابایت)…" -f $files.Count, ((Get-Item $tarPath).Length / 1MB))
    $reply = & cmd.exe /c $cmdLine 2>&1
    $rc = $LASTEXITCODE
    Remove-Item $tarPath, $listPath -Force -ErrorAction SilentlyContinue

    if ($rc -ne 0) {
        Write-Log ("ارسال نشد (کدِ {0}): {1}" -f $rc, ($reply -join " ")) "ERROR"
        return 0
    }
    Write-Log ("سرور: {0}" -f ($reply -join " "))

    foreach ($f in $files) {
        Move-Item -Path $f.FullName -Destination (Join-Path $P.Sent $f.Name) -Force
    }
    Write-Log "$($files.Count) فایل ارسال و بایگانی شد."
    return $files.Count
}

# ------------------------------------------------------------------ main ----
function Invoke-Cycle {
    param($Cfg, $P)
    Invoke-Pull -Cfg $Cfg -P $P | Out-Null

    $inv = Get-Inventory -P $P
    if ($inv.InboxCount -eq 0) { Write-Log "inbox خالی است — کاری نیست."; return }

    Write-Log ("شروع: {0} خام در inbox، {1} خروجیِ آماده." -f $inv.InboxCount, $inv.OutboxCount)
    $res = Invoke-Compositing -Cfg $Cfg -P $P
    Write-Log ("ادیت تمام شد: ساخته‌شده={0} خطا={1} در {2} بچ." -f $res.Ok, $res.Failed, $res.Batches)

    if ($Cfg.auto_push -and -not $NoPush) {
        Invoke-Push -Cfg $Cfg -P $P | Out-Null
    } else {
        Write-Log "ارسالِ خودکار خاموش است — خروجی‌ها در outbox می‌مانند."
    }
}

$cfg = Get-AgentConfig -Path $ConfigPath
$script:P = Get-Paths -Cfg $cfg
New-Folders -P $script:P

if ($Status) {
    $inv = Get-Inventory -P $script:P
    Write-Host ("inbox={0}  outbox={1}  sent={2}  کارِ موردانتظار={3}" -f `
                $inv.InboxCount, $inv.OutboxCount, $inv.SentCount, $inv.ExpectedTotal)
    return
}

$problems = Test-Preflight -Cfg $cfg -P $script:P
if ($problems.Count -gt 0) {
    Write-Log "بررسیِ آمادگی رد شد:" "ERROR"
    foreach ($p in $problems) { Write-Log "  • $p" "ERROR" }
    if ($Preflight) { return }
    exit 1
}
if ($Preflight) { Write-Log "همه‌چیز آماده است ✅"; return }

if ($Watch) {
    $sleep = [int]$cfg.poll_seconds
    if ($sleep -lt 15) { $sleep = 60 }
    Write-Log "حالتِ پایش روشن شد (هر $sleep ثانیه)."
    while ($true) {
        try { Invoke-Cycle -Cfg $cfg -P $script:P }
        catch { Write-Log ("دورِ ناموفق: {0}" -f $_.Exception.Message) "ERROR" }
        Start-Sleep -Seconds $sleep
    }
} else {
    Invoke-Cycle -Cfg $cfg -P $script:P
}
