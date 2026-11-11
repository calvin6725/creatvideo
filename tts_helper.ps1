param(
    [ValidateSet('voices', 'speak')][string]$Mode,
    [string]$TextBase64 = '',
    [string]$VoiceBase64 = '',
    [string]$OutputPath = ''
)

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Mode -eq 'voices') {
        $synth.GetInstalledVoices() | ForEach-Object {
            $_.VoiceInfo.Name + "|" + $_.VoiceInfo.Culture.Name
        }
        exit 0
    }

    $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TextBase64))
    $voice = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($VoiceBase64))
    try { $synth.SelectVoice($voice) } catch { }
    $synth.SetOutputToWaveFile($OutputPath)
    $synth.Speak($text)
} finally {
    if ($synth) { $synth.Dispose() }
}
