# English narrator voice: Kokoro-82M vs Pocket TTS

Backlog item: a more realistic, still super fast, fully local English voice for the DM. Tried Kokoro-82M first.

## Result in one line

Kokoro-82M (ONNX, int8) is **as fast as Pocket or a bit faster** on this CPU-only machine, uses **~35% less memory**, loads in **~1 s instead of ~4 s**, and has a much larger choice of English voices, including several male British and American narrator voices. **Update:** after listening, Nick chose Kokoro. It is now the default engine whenever it is installed (Pocket otherwise; `DND_TTS_ENGINE=pocket` forces Pocket). Default voice `am_adam`; Settings > Narrator voice offers Adam, Fenrir, Onyx and George, each with a play button for the same sample lines.

## Measurements (this machine: Windows 11, CPU only, Python 3.12, onnxruntime 1.32)

Test text: a ~40-word and a ~120-word DM narration paragraph. Each number is a call to the running HTTP server (what the display actually does), second and later calls, best of two runs. Other sessions were using the CPU at times, so expect ±30% noise; one run during heavy background load was 5x slower for both engines.

| | Pocket TTS (alba) | Kokoro int8 | Kokoro fp32 |
|---|---|---|---|
| Install on disk | ~950 MB (venv with torch, plus weights) | ~120 MB models + a light venv | ~355 MB models + a light venv |
| Cold start (process start to first clip ready) | ~5.3 s | **~1.8 s** | ~2.0 s (in-process load + warm-up, not via server) |
| 40 words: synth time (audio length) | 2.9-3.5 s (12 s) | **2.4-2.8 s** (14 s) | ~3.8 s (13.7 s) |
| 40 words: real-time factor | 0.23-0.29 | **0.17-0.20** | 0.28-0.32 |
| 120 words: synth time (audio length) | 8.6-9.8 s (34 s) | **7.5-9.4 s** (39.7 s) | ~14.3 s (38.5 s) |
| 120 words: real-time factor | 0.25-0.29 | **0.19-0.24** | 0.37-0.39 |
| Time to first audio, as the app uses it (whole clip) | = total | = total | = total |
| Time to first audio if sentences were streamed | n/a (one pass) | **~0.7 s** (first sentence) | ~1.2 s |
| Memory (server working set) | ~925 MB (peak 990) | **~640 MB** (peak 655) | not measured |

Reading: a short block (~40 words) is ready in about 2.5 s with either engine; a long 120-word block takes 7-9 s. Neither engine is a clear speed winner on whole clips, but Kokoro synthesizes sentence by sentence, so it could start talking after under a second if the display played sentences as they arrive. That needs a change in the display page (not done here, see "What is left"). The int8 model is the better default: faster and 3.5x smaller than fp32; the fp32 file is used only if the int8 file is missing (or set `DND_KOKORO_MODEL`).

## Samples (docs/research/tts-samples/)

Same ~9 s of narration in each voice, 16 kHz mono WAV, about 2.5 MB in total:
`pocket-alba.wav` (current), `kokoro-bm_george`, `bm_lewis`, `bm_fable`, `bm_daniel` (British male), `am_adam`, `am_michael`, `am_onyx`, `am_fenrir` (American male).

**Honest note on quality:** I generated these but could not listen to them (no audio device in this session), so the voice ranking below comes from Kokoro's published voice grades and community reports, not from my ears. Nick should play the nine files and pick. Expected character: Kokoro sounds clearly smoother and more natural in rhythm than most small local models, but it is a "good audiobook reader" voice, not an actor: it does not do shouting, whispering or per-character acting, and very long sentences can flatten out. Pocket (7/10 by Nick) has its own cloned-voice charm; Kokoro should be judged side by side.

## Recommended voices for a dramatic DM

1. **`bm_george`**: British male, deep and steady; the default here. Best candidate for the classic fireside-storyteller feel.
2. **`bm_fable`**: British male, the one the model card describes as the storyteller voice; slightly lighter.
3. **`am_onyx`** or **`am_adam`**: darker American male, if a British accent is not wanted. `am_fenrir` and `am_michael` are the other American choices.

Switch in Settings, or with `DND_KOKORO_VOICE=am_onyx` (env wins). The voice is part of the clip cache key, so changing it never replays old clips.

## How to use it

```bash
# one-time: the venv and models live outside git
python -m venv ~/.cache/claude-dnd/tts-kokoro/venv
~/.cache/claude-dnd/tts-kokoro/venv/Scripts/python.exe -m pip install kokoro-onnx
# download kokoro-v1.0.int8.onnx and voices-v1.0.bin from
# github.com/thewh1teagle/kokoro-onnx (release model-files-v1.0) into
# ~/.cache/claude-dnd/tts-kokoro/models/

DND_TTS_ENGINE=kokoro bash skills/dnd/display/start-display.sh
```

The Kokoro server (`skills/dnd/display/local_tts/kokoro_server.py`) mirrors the Pocket server: `POST /synth`, `GET /health`, `POST /shutdown`, 24 kHz 16-bit mono PCM. It listens on port 8767 (Pocket stays on 8766); `DND_LOCAL_TTS_PORT` overrides both. `kokoro-onnx` pulls in `espeakng-loader` (a native DLL); it loaded fine under this machine's application control.

## Other fully local candidates (not installed)

Only Kokoro was installed and measured. The others are ruled out on documented size and speed for CPU-only, not on a test run here:

- **Piper**: very fast (RTF about 0.05) and tiny, ONNX, but its voices are noticeably more robotic than Kokoro; no better than Pocket for drama. Fallback if speed ever becomes the problem.
- **Orpheus 3B, Dia, Sesame CSM, Chatterbox, F5-TTS, XTTS-v2, Parler-TTS**: more expressive, but they are 0.5-3 billion parameters and need torch; on CPU they run slower than real time, which fails "a short block ready in a few seconds".
- **Supertonic / sherpa-onnx based engines, torch XPU**: need native DLLs that Windows application control blocks here.
- **Kokoro via the official `kokoro` package (torch)**: same model, heavier install, no gain over the ONNX route.

## What is left

- Nick listens to the samples and picks the default engine/voice. Flipping the default is a one-line change in `tts.py` (`_engine`) and `local_fallback.py` (`engine`).
- Sentence streaming for Kokoro (first audio in under a second) needs a change in the display page's audio queue, which was out of scope here.
- Per-voice character acting (different voices for NPCs) would be possible with Kokoro since the server already accepts a `voice` per request.
