param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$RootPrefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$Utf8 = New-Object Text.UTF8Encoding($false)

function Write-Bytes {
    param($Context, [int]$StatusCode, [string]$ContentType, [byte[]]$Bytes)
    $Context.Response.StatusCode = $StatusCode
    $Context.Response.ContentType = $ContentType
    $Context.Response.ContentLength64 = $Bytes.Length
    $Context.Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
    $Context.Response.Close()
}

function Write-Json {
    param($Context, [int]$StatusCode, $Value)
    $json = $Value | ConvertTo-Json -Depth 5 -Compress
    Write-Bytes $Context $StatusCode 'application/json; charset=utf-8' $Utf8.GetBytes($json)
}

function Get-LocalVoices {
    $synth = $null
    try {
        $synth = New-Object -ComObject SAPI.SpVoice
        $items = @()
        foreach ($token in @($synth.GetVoices())) {
            $language = ''
            try {
                $lcid = [Convert]::ToInt32($token.GetAttribute('Language'), 16)
                $language = [Globalization.CultureInfo]::GetCultureInfo($lcid).DisplayName
            } catch {}
            $items += [PSCustomObject]@{
                name = $token.GetDescription()
                language = $language
            }
        }
        return @($items | Sort-Object @{ Expression = { if ($_.language -match 'Chinese|zh') { 0 } else { 1 } } }, name)
    } finally {
        if ($synth) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($synth) }
    }
}

function New-SpeechWave {
    param([string]$Text, [string]$VoiceName)
    $path = Join-Path ([IO.Path]::GetTempPath()) ('voice-lab-' + [Guid]::NewGuid().ToString('N') + '.wav')
    $synth = $null
    $stream = $null
    try {
        $synth = New-Object -ComObject SAPI.SpVoice
        if ($VoiceName) {
            $selected = @($synth.GetVoices()) | Where-Object { $_.GetDescription() -eq $VoiceName } | Select-Object -First 1
            if ($selected) { $synth.Voice = $selected }
        }
        $synth.Rate = 0
        $synth.Volume = 100
        $stream = New-Object -ComObject SAPI.SpFileStream
        $stream.Open($path, 3, $false)
        $synth.AudioOutputStream = $stream
        [void]$synth.Speak($Text, 0)
        $stream.Close()
        $stream = $null
        return [IO.File]::ReadAllBytes($path)
    } finally {
        if ($stream) {
            try { $stream.Close() } catch {}
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($stream)
        }
        if ($synth) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($synth) }
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
}

function Get-ContentType {
    param([string]$Path)
    switch ([IO.Path]::GetExtension($Path).ToLowerInvariant()) {
        '.html' { return 'text/html; charset=utf-8' }
        '.css'  { return 'text/css; charset=utf-8' }
        '.js'   { return 'application/javascript; charset=utf-8' }
        '.json' { return 'application/json; charset=utf-8' }
        '.svg'  { return 'image/svg+xml' }
        '.png'  { return 'image/png' }
        '.jpg'  { return 'image/jpeg' }
        '.jpeg' { return 'image/jpeg' }
        '.ico'  { return 'image/x-icon' }
        default { return 'application/octet-stream' }
    }
}

$listener = New-Object Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()
Write-Host "Voice Lab is running at http://localhost:$Port/"

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        try {
            $method = $context.Request.HttpMethod
            $path = $context.Request.Url.AbsolutePath

            if ($method -eq 'GET' -and $path -eq '/api/voices') {
                $voices = @(Get-LocalVoices)
                Write-Json $context 200 @{ available = ($voices.Count -gt 0); voices = $voices }
                continue
            }

            if ($method -eq 'POST' -and $path -eq '/api/tts') {
                if ($context.Request.ContentLength64 -gt 262144) { throw 'Request body is too large.' }
                $reader = New-Object IO.StreamReader($context.Request.InputStream, $Utf8)
                try { $payload = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
                $text = [string]$payload.text
                $voice = [string]$payload.voice
                if ([string]::IsNullOrWhiteSpace($text)) { throw 'Narration text is required.' }
                if ($text.Length -gt 12000) { throw 'Narration text cannot exceed 12000 characters.' }
                $wave = New-SpeechWave $text.Trim() $voice
                Write-Bytes $context 200 'audio/wav' $wave
                continue
            }

            if ($method -ne 'GET' -and $method -ne 'HEAD') {
                Write-Json $context 405 @{ error = 'Method not allowed.' }
                continue
            }

            $relative = [Uri]::UnescapeDataString($path.TrimStart('/'))
            if ([string]::IsNullOrWhiteSpace($relative)) { $relative = 'index.html' }
            $filePath = [IO.Path]::GetFullPath((Join-Path $Root $relative))
            if (-not $filePath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
                Write-Json $context 404 @{ error = 'File not found.' }
                continue
            }

            $bytes = [IO.File]::ReadAllBytes($filePath)
            if ($method -eq 'HEAD') { $bytes = [byte[]]@() }
            Write-Bytes $context 200 (Get-ContentType $filePath) $bytes
        } catch {
            if ($context.Response.OutputStream.CanWrite) {
                try { Write-Json $context 500 @{ error = $_.Exception.Message } } catch { try { $context.Response.Abort() } catch {} }
            }
        }
    }
} finally {
    $listener.Stop()
    $listener.Close()
}
