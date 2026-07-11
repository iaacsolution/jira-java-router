"""
Transcription audio avec faster-whisper (large-v3, float16, CUDA)
==================================================================
Installation :
    pip install faster-whisper

Usage :
    python transcribe.py audio.mp3
    python transcribe.py audio.wav --language fr --output transcription.txt
"""

import sys
import argparse
import time
from pathlib import Path
from faster_whisper import WhisperModel


def transcribe(audio_path: str, language: str = None, output: str = None) -> str:

    print(f"Chargement du modèle large-v3 (float16, CUDA)...")
    t0 = time.perf_counter()

    model = WhisperModel(
        "small",                # small = rapide sur CPU, bonne qualité
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
    )

    load_time = time.perf_counter() - t0
    print(f"Modèle chargé en {load_time:.1f}s")

    # ── Transcription ────────────────────────────────────────────────────────
    print(f"Transcription de : {audio_path}")
    t1 = time.perf_counter()

    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        language=language,      # None = détection automatique
        vad_filter=True,        # supprime les silences
        vad_parameters={"min_silence_duration_ms": 500},
    )

    print(f"Langue détectée : {info.language} (probabilité : {info.language_probability:.0%})")
    print(f"Durée audio     : {info.duration:.1f}s")
    print()

    # ── Boucle sur les segments pour reconstruire la transcription ───────────
    lines   = []
    full    = []

    for segment in segments:
        timestamp = f"[{segment.start:6.2f}s -> {segment.end:6.2f}s]"
        text      = segment.text.strip()

        print(f"{timestamp}  {text}")

        lines.append(f"{timestamp}  {text}")
        full.append(text)

    transcription_time = time.perf_counter() - t1
    full_text = " ".join(full)

    # ── Résumé ───────────────────────────────────────────────────────────────
    rtf = transcription_time / info.duration  # Real-Time Factor
    print()
    print(f"── Résumé ──────────────────────────────────")
    print(f"Durée transcription : {transcription_time:.1f}s")
    print(f"RTF (temps réel)    : {rtf:.2f}x  {'✅ temps réel' if rtf < 1 else '⚠️ plus lent que temps réel'}")
    print(f"Caractères          : {len(full_text)}")

    # ── Export fichier ───────────────────────────────────────────────────────
    if output:
        Path(output).write_text("\n".join(lines), encoding="utf-8")
        print(f"Transcription sauvegardée : {output}")

    return full_text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcription faster-whisper large-v3")
    parser.add_argument("audio",     help="Chemin du fichier audio")
    parser.add_argument("--language",default=None, help="Langue (fr, en, ...) — auto si absent")
    parser.add_argument("--output",  default=None, help="Fichier de sortie .txt")
    args = parser.parse_args()

    result = transcribe(args.audio, args.language, args.output)
    print(f"\n── Transcription complète ──\n{result}")
