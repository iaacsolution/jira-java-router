"""
Enregistrement micro + transcription automatique (PyAudio + Whisper)
====================================================================
Installation :
    pip install pyaudio faster-whisper
    Si pyaudio échoue : pip install pipwin && pipwin install pyaudio

Usage :
    python record_and_transcribe.py              (appuyez Entrée pour arrêter)
    python record_and_transcribe.py --output reunion.txt
"""

import argparse
import tempfile
import wave
import numpy as np
import pyaudio
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
CHUNK       = 1024
CHANNELS    = 1
FORMAT      = pyaudio.paInt16


def record_until_enter() -> str:
    """Enregistre le micro jusqu'à Entrée. Retourne le chemin du fichier WAV."""
    p      = pyaudio.PyAudio()
    frames = []

    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    print("🎙️  Enregistrement en cours... (appuyez sur Entrée pour arrêter)")

    import threading
    stop = threading.Event()

    def read_audio():
        while not stop.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            except Exception:
                break

    thread = threading.Thread(target=read_audio, daemon=True)
    thread.start()
    input()
    stop.set()
    thread.join(timeout=1)

    stream.stop_stream()
    stream.close()
    p.terminate()

    duration = len(frames) * CHUNK / SAMPLE_RATE
    print(f"⏹️  Enregistrement arrêté. Durée : {duration:.1f}s")

    # Sauvegarde WAV temporaire
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))

    return tmp.name


def transcribe(wav_path: str, language: str = "fr") -> str:
    print("Chargement du modèle Whisper small...")
    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)

    print("Transcription en cours...\n")
    segments, info = model.transcribe(
        wav_path,
        language=language,
        beam_size=5,
        vad_filter=False,
    )

    lines = []
    for segment in segments:
        line = f"[{segment.start:5.1f}s]  {segment.text.strip()}"
        print(line)
        lines.append(line)

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="fr")
    parser.add_argument("--output",   default=None)
    args = parser.parse_args()

    wav      = record_until_enter()
    result   = transcribe(wav, args.language)

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"\n✅ Sauvegardé : {args.output}")

    print(f"\n── Transcription complète ──\n{result}")
