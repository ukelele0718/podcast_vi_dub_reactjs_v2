import sys

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Root project
ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT / "storage" / "jobs"
VALTEC_SRC = ROOT / "valtec-tts-src"

app = FastAPI(title="Podcast VI Dub API (local)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------- helpers ---------------
def _run(cmd, cwd: Path, env: Optional[dict] = None) -> str:
    p = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stdout)
    return p.stdout


def _ensure_dirs(job_dir: Path):
    (job_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "logs").mkdir(parents=True, exist_ok=True)


def _read_status(job_dir: Path) -> dict:
    """Read job status + step info."""
    status = "unknown"
    step = ""
    if (job_dir / "status.txt").exists():
        status = (job_dir / "status.txt").read_text("utf-8").strip()
    if (job_dir / "step.txt").exists():
        step = (job_dir / "step.txt").read_text("utf-8").strip()

    files = []
    out_dir = job_dir / "outputs"
    if out_dir.exists():
        for p in out_dir.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(out_dir)))

    # Read texts if available
    en_text = ""
    vi_text = ""
    en_path = out_dir / "podcast_en.txt"
    vi_path = out_dir / "podcast_vi.txt"
    if en_path.exists():
        en_text = en_path.read_text("utf-8")
    if vi_path.exists():
        vi_text = vi_path.read_text("utf-8")

    return {
        "job_id": job_dir.name,
        "status": status,
        "step": step,
        "files": sorted(files),
        "en_text": en_text,
        "vi_text": vi_text,
    }


def _run_pipeline_bg(in_path: Path, job_dir: Path, speaker: str, device: str, youtube_url: str = None, spotify_url: str = None):
    """Run pipeline in background thread (in-process, no subprocess)."""
    try:
        os.environ["TTS_SPEAKER"] = speaker
        os.environ["TTS_DEVICE"] = device
        from app.pipeline import main as run_pipeline, is_youtube_url, download_youtube_audio, is_spotify_url, download_spotify_audio
        
        # If youtube_url provided, download first
        if youtube_url and is_youtube_url(youtube_url):
            in_path = download_youtube_audio(youtube_url, job_dir / "inputs" / "youtube_audio", job_dir)
        # If spotify_url provided, download from Spotify
        elif spotify_url and is_spotify_url(spotify_url):
            in_path = download_spotify_audio(spotify_url, job_dir / "inputs" / "spotify_audio", job_dir)
        
        run_pipeline(in_path, job_dir)
        (job_dir / "status.txt").write_text("done", encoding="utf-8")
        (job_dir / "step.txt").write_text("Hoàn tất!", encoding="utf-8")
    except Exception as e:
        import traceback
        (job_dir / "status.txt").write_text("error", encoding="utf-8")
        (job_dir / "logs" / "error.log").write_text(traceback.format_exc(), encoding="utf-8")


# --------------- endpoints ---------------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/jobs")
def list_jobs():
    """List all job ids, newest first."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    jobs = sorted(
        [d.name for d in JOBS_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    return {"jobs": jobs}


@app.post("/jobs")
async def create_job(
    file: UploadFile = File(None),
    youtube_url: str = Form(None),
    spotify_url: str = Form(None),
    speaker: str = Form("SF"),
    device: str = Form("cpu"),
):
    # Validate input: need either file, youtube_url, or spotify_url
    if not file and not youtube_url and not spotify_url:
        raise HTTPException(status_code=400, detail="Cần chọn file audio hoặc nhập link YouTube/Spotify")
    
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = time.strftime("job_%Y%m%d_%H%M%S")
    job_dir = JOBS_DIR / job_id
    _ensure_dirs(job_dir)

    in_path = None
    
    # If file provided, save it
    if file and file.filename:
        in_path = job_dir / "inputs" / "input.mp3"
        with open(in_path, "wb") as f:
            f.write(await file.read())

    (job_dir / "status.txt").write_text("running", encoding="utf-8")
    
    if youtube_url:
        (job_dir / "step.txt").write_text("Đang chuẩn bị tải từ YouTube...", encoding="utf-8")
    elif spotify_url:
        (job_dir / "step.txt").write_text("Đang chuẩn bị tải từ Spotify...", encoding="utf-8")
    else:
        (job_dir / "step.txt").write_text("Đang khởi tạo...", encoding="utf-8")

    # Run pipeline in background thread so API returns immediately
    t = threading.Thread(
        target=_run_pipeline_bg,
        args=(in_path, job_dir, speaker, device, youtube_url, spotify_url),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return _read_status(job_dir)


@app.get("/jobs/{job_id}/download")
def download(job_id: str, path: str):
    job_dir = JOBS_DIR / job_id
    base = (job_dir / "outputs").resolve()
    file_path = (job_dir / "outputs" / path).resolve()

    if base not in file_path.parents and file_path != base:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(str(file_path), filename=file_path.name)
