"""Anlatici ses ornekleri (Kokoro TTS, Apache-2.0 lisansli, cevrimdisi)."""
import os
import subprocess

import numpy as np
import soundfile as sf
from kokoro import KPipeline

TEXT = ("Ninety-three million miles away, our star is restless. "
        "Beneath its glowing surface, twisted magnetic fields store unimaginable energy. "
        "And when they finally snap, the Sun hurls billions of tons of plasma into space, "
        "a solar storm racing toward Earth.")
VOICES = {"am_michael": "a", "am_fenrir": "a", "bm_george": "b", "bm_fable": "b"}
OUT = os.environ.get("OUT", "ses-ornekleri")
os.makedirs(OUT, exist_ok=True)
pipes = {}
for i, (voice, lang) in enumerate(VOICES.items(), 1):
    pipe = pipes.setdefault(lang, KPipeline(lang_code=lang))
    audio = np.concatenate([a for _, _, a in pipe(TEXT, voice=voice, speed=0.95)])
    wav = f"{OUT}/{voice}.wav"
    sf.write(wav, audio, 24000)
    mp3 = f"{OUT}/ses-{i}-{voice}.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-af", "loudnorm=I=-16",
                    "-ar", "44100", "-b:a", "160k", mp3], check=True)
    os.remove(wav)
    print(mp3, f"{len(audio) / 24000:.1f} sn")
