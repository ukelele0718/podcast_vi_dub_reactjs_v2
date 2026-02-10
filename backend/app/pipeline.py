import os
import subprocess
import sys
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

ROOT = Path(__file__).resolve().parents[2]
VALTEC_SRC = ROOT / "valtec-tts-src"

MODEL_TRANSLATE = "facebook/nllb-200-distilled-600M"


def _write_step(job_dir: Path, msg: str):
    """Write current step to step.txt so the frontend can poll it."""
    (job_dir / "step.txt").write_text(msg, encoding="utf-8")


def run_ffmpeg_to_16k(in_path: Path, out_path: Path):
    cmd = ["ffmpeg", "-y", "-i", str(in_path), "-ac", "1", "-ar", "16000", str(out_path)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)


def chunk_text(text: str, max_chars: int = 260):
    parts, buf = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        while len(line) > max_chars:
            parts.append(line[:max_chars].strip())
            line = line[max_chars:].strip()
        if len(buf) + len(line) + 1 > max_chars:
            if buf.strip():
                parts.append(buf.strip())
            buf = line
        else:
            buf = (buf + " " + line).strip()
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_text_translate(text: str, max_chars: int = 900):
    """Chunk text into larger blocks for translation (like v1)."""
    parts, buf = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(buf) + len(line) + 1 > max_chars:
            if buf.strip():
                parts.append(buf.strip())
            buf = line
        else:
            buf = (buf + " " + line).strip()
    if buf.strip():
        parts.append(buf.strip())
    return parts


def tts_speak(vi_text: str, out_wav: Path, speaker: str, device: str, job_dir: Path):
    sys.path.insert(0, str(VALTEC_SRC))
    from valtec_tts import TTS  # noqa

    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"

    # Thử GPU trước, fallback CPU nếu lỗi
    tts = None
    if device == "cuda":
        try:
            tts = TTS(device="cuda")
            print("TTS: Using GPU")
        except Exception as e:
            print(f"TTS GPU failed: {e}, falling back to CPU")
            torch.cuda.empty_cache()
            device = "cpu"
    
    if tts is None:
        tts = TTS(device="cpu")
        print("TTS: Using CPU")

    seg_dir = out_wav.parent / "tts_segs"
    seg_dir.mkdir(parents=True, exist_ok=True)

    chunks = chunk_text(vi_text, 260)
    seg_files = []
    total = len(chunks)
    tts_cpu_fallback = False
    for i, ch in enumerate(chunks, 1):
        _write_step(job_dir, f"TTS đoạn {i}/{total}...")
        seg = seg_dir / f"seg_{i:04d}.wav"
        if not seg.exists():
            try:
                tts.speak(ch, speaker=speaker, speed=1.0, output_path=str(seg))
            except RuntimeError as e:
                if "out of memory" in str(e).lower() and not tts_cpu_fallback:
                    print(f"TTS GPU OOM at chunk {i}, switching to CPU...")
                    torch.cuda.empty_cache()
                    del tts
                    tts = TTS(device="cpu")
                    tts_cpu_fallback = True
                    try:
                        tts.speak(ch, speaker=speaker, speed=1.0, output_path=str(seg))
                    except Exception as e2:
                        print(f"TTS CPU error chunk {i}: {e2}")
                        continue
                else:
                    print(f"TTS error chunk {i}: {e}")
                    continue
            except Exception as e:
                print(f"TTS error chunk {i}: {e}")
                continue
        seg_files.append(seg)

    if not seg_files:
        raise RuntimeError("Không tạo được segment wav nào.")

    # concat
    list_txt = out_wav.parent / "list.txt"
    with open(list_txt, "w", encoding="utf-8") as f:
        for p in seg_files:
            f.write(f"file '{p.resolve()}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(out_wav)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)


def wav_to_mp3(in_wav: Path, out_mp3: Path):
    cmd = ["ffmpeg", "-y", "-i", str(in_wav), "-codec:a", "libmp3lame", "-qscale:a", "3", str(out_mp3)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)


def translate_en2vi(text: str, job_dir: Path) -> str:
    """Translate English to Vietnamese using NLLB, chunked for stability."""
    tokenizer = AutoTokenizer.from_pretrained(MODEL_TRANSLATE)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_TRANSLATE)

    # Thử GPU trước, fallback CPU nếu lỗi
    device = "cpu"
    if torch.cuda.is_available():
        try:
            model = model.to("cuda")
            device = "cuda"
            print("Translate: Using GPU")
        except Exception as e:
            print(f"Translate GPU failed: {e}, falling back to CPU")
            torch.cuda.empty_cache()
            model = model.to("cpu")
    else:
        model = model.to("cpu")
    model.eval()

    tokenizer.src_lang = "eng_Latn"
    forced_bos = tokenizer.convert_tokens_to_ids("vie_Latn")

    chunks = chunk_text_translate(text, max_chars=900)
    vi_parts = []
    cpu_fallback = False
    for i, ch in enumerate(chunks, 1):
        _write_step(job_dir, f"Dịch đoạn {i}/{len(chunks)}...")
        inputs = tokenizer(ch, return_tensors="pt", truncation=True, max_length=1024)
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

        try:
            with torch.inference_mode():
                out = model.generate(
                    **inputs, max_new_tokens=256, num_beams=4,
                    forced_bos_token_id=forced_bos,
                )
        except RuntimeError as e:
            if "out of memory" in str(e).lower() and not cpu_fallback:
                print(f"Translate GPU OOM at chunk {i}, switching to CPU...")
                torch.cuda.empty_cache()
                model = model.to("cpu")
                cpu_fallback = True
                inputs = {k: v.to("cpu") for k, v in inputs.items()}
                with torch.inference_mode():
                    out = model.generate(
                        **inputs, max_new_tokens=256, num_beams=4,
                        forced_bos_token_id=forced_bos,
                    )
            else:
                raise
        vi_parts.append(tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip())

    return "\n".join(vi_parts).strip()


def main(in_audio: Path, job_dir: Path):
    out_dir = job_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: FFmpeg
    _write_step(job_dir, "Chuyển đổi audio → 16kHz WAV...")
    print("1) ffmpeg -> 16k wav")
    wav16 = out_dir / "podcast_en_16k.wav"
    run_ffmpeg_to_16k(in_audio, wav16)

    # Step 2: ASR
    _write_step(job_dir, "Nhận dạng giọng nói (Whisper)...")
    print("2) ASR (English) faster-whisper")
    from faster_whisper import WhisperModel  # noqa: lazy import
    
    # Thử GPU trước, fallback CPU nếu lỗi
    asr_model = None
    if torch.cuda.is_available():
        try:
            asr_model = WhisperModel("small", device="cuda", compute_type="float16")
            print("ASR: Using GPU")
        except Exception as e:
            print(f"ASR GPU failed: {e}, falling back to CPU")
            torch.cuda.empty_cache()
    
    if asr_model is None:
        asr_model = WhisperModel("small", device="cpu", compute_type="int8")
        print("ASR: Using CPU")
    
    try:
        segments, _info = asr_model.transcribe(str(wav16), language="en", vad_filter=True)
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            print(f"ASR GPU OOM, retrying with CPU...")
            torch.cuda.empty_cache()
            del asr_model
            asr_model = WhisperModel("small", device="cpu", compute_type="int8")
            segments, _info = asr_model.transcribe(str(wav16), language="en", vad_filter=True)
        else:
            raise
    en_lines = [s.text.strip() for s in segments if (s.text or "").strip()]
    en_txt = "\n".join(en_lines).strip()
    (out_dir / "podcast_en.txt").write_text(en_txt, encoding="utf-8")
    
    # Giải phóng GPU memory sau ASR
    del asr_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Step 3: Translate
    _write_step(job_dir, "Dịch EN → VI (NLLB)...")
    print("3) Translate EN->VI (NLLB)")
    vi_txt = translate_en2vi(en_txt, job_dir)
    (out_dir / "podcast_vi.txt").write_text(vi_txt, encoding="utf-8")
    
    # Giải phóng GPU memory sau Translate
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Step 4: TTS
    _write_step(job_dir, "Tổng hợp giọng nói tiếng Việt...")
    print("4) TTS VI (valtec-tts)")
    speaker = os.getenv("TTS_SPEAKER", "SF")
    tts_device = os.getenv("TTS_DEVICE", "cpu")
    out_wav = out_dir / f"podcast_vi_{speaker}.wav"
    tts_speak(vi_txt, out_wav, speaker=speaker, device=tts_device, job_dir=job_dir)

    # Step 5: Convert to MP3
    _write_step(job_dir, "Chuyển sang MP3...")
    out_mp3 = out_dir / f"podcast_vi_{speaker}.mp3"
    wav_to_mp3(out_wav, out_mp3)

    print("DONE")
    print("Outputs:", out_dir)


if __name__ == "__main__":
    in_audio = Path(sys.argv[1]).resolve()
    job_dir = Path(sys.argv[2]).resolve()
    main(in_audio, job_dir)
