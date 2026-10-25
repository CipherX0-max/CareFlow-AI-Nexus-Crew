# Local Whisper speed comparison

This is an engineering timing check, not a language-quality evaluation. It uses one short synthetic English sentence on the current
Windows CPU configuration. Both model caches were already present, and the benchmark forces `local_files_only=True` so it cannot
download weights. It measures the speech transcription pass only; the optional Whisper English-translation pass is not included.

## Reproduce on Windows

Generate a WAV locally with Windows SAPI (no network service):

```powershell
$dir = Join-Path (Get-Location) '.pytest-tmp\stt-benchmark'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile((Join-Path $dir 'synthetic_en.wav'))
$synth.Speak('This is a synthetic speed test. I have a mild headache for two days, but no fever and no cough.')
$synth.Dispose()
```

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m backend.careflow.scripts.benchmark_whisper_speed .pytest-tmp\stt-benchmark\synthetic_en.wav --reference "This is a synthetic speed test. I have a mild headache for two days, but no fever and no cough."
```

## Result recorded for this checkout

Settings were `large-v3` (active `.env`), `small`, CPU, and `int8`. Each was loaded once, then run on the same WAV. Results from
one run:

| Model | Load time | Transcription time | Detected language confidence | WER |
|---|---:|---:|---:|---:|
| `large-v3` | 8.161 s | 24.576 s | English, 0.995 | 0.0 |
| `small` | 1.098 s | 3.488 s | English, 0.997 | 0.0 |

Both outputs matched the synthetic reference exactly, including “no fever” and “no cough.” This single English sentence does not
measure Hindi, Marathi, Bengali, Hinglish, accents, noise, or clinical transcription quality. The measured `small` transcription
was about 7.0× faster for this sample; multilingual model choice should be based on a broader local evaluation before changing the
configured model. The app keeps the active model setting configurable and reuses one loaded model instance per backend process.
