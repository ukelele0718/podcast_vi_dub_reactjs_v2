import { useState, useEffect, useRef, useCallback } from 'react'
import './App.css'

const API = 'http://localhost:8000'

function App() {
  const [inputType, setInputType] = useState('file') // 'file', 'youtube', or 'spotify'
  const [file, setFile] = useState(null)
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [spotifyUrl, setSpotifyUrl] = useState('')
  const [speaker, setSpeaker] = useState('SF')
  const [device, setDevice] = useState('cpu')
  const [jobId, setJobId] = useState(null)
  const [job, setJob] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState([])
  const pollRef = useRef(null)
  const fileInputRef = useRef(null)

  // Load job history on mount
  useEffect(() => {
    fetch(`${API}/jobs`)
      .then(r => r.json())
      .then(d => setHistory(d.jobs || []))
      .catch(() => {})
  }, [])

  // Poll job status
  const pollJob = useCallback((id) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/jobs/${id}`)
        const data = await r.json()
        setJob(data)
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(pollRef.current)
          pollRef.current = null
          // refresh history
          fetch(`${API}/jobs`).then(r => r.json()).then(d => setHistory(d.jobs || []))
        }
      } catch { /* ignore */ }
    }, 2000)
  }, [])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    
    // Validate input
    if (inputType === 'file' && !file) { 
      setError('Vui lòng chọn file audio.'); 
      return 
    }
    if (inputType === 'youtube' && !youtubeUrl.trim()) { 
      setError('Vui lòng nhập link YouTube.'); 
      return 
    }
    if (inputType === 'spotify' && !spotifyUrl.trim()) { 
      setError('Vui lòng nhập link Spotify.'); 
      return 
    }
    
    setError('')
    setSubmitting(true)
    setJob(null)

    const fd = new FormData()
    if (inputType === 'file' && file) {
      fd.append('file', file)
    }
    if (inputType === 'youtube' && youtubeUrl.trim()) {
      fd.append('youtube_url', youtubeUrl.trim())
    }
    if (inputType === 'spotify' && spotifyUrl.trim()) {
      fd.append('spotify_url', spotifyUrl.trim())
    }
    fd.append('speaker', speaker)
    fd.append('device', device)

    try {
      const r = await fetch(`${API}/jobs`, { method: 'POST', body: fd })
      if (!r.ok) { throw new Error(await r.text()) }
      const data = await r.json()
      setJobId(data.job_id)
      const stepMap = {
        'youtube': 'Đang tải từ YouTube...',
        'spotify': 'Đang tải từ Spotify...',
        'file': 'Đang khởi tạo...'
      }
      setJob({ 
        job_id: data.job_id, 
        status: 'running', 
        step: stepMap[inputType] || 'Đang khởi tạo...', 
        files: [] 
      })
      pollJob(data.job_id)
    } catch (err) {
      setError(err.message || 'Lỗi kết nối server')
    } finally {
      setSubmitting(false)
    }
  }

  const loadJob = async (id) => {
    setJobId(id)
    setError('')
    try {
      const r = await fetch(`${API}/jobs/${id}`)
      const data = await r.json()
      setJob(data)
      if (data.status === 'running') pollJob(id)
    } catch { setError('Không tải được job') }
  }

  const downloadUrl = (path) => `${API}/jobs/${jobId}/download?path=${encodeURIComponent(path)}`

  const audioFile = job?.files?.find(f => f.endsWith('.mp3')) || job?.files?.find(f => f.endsWith('.wav') && !f.includes('16k'))

  return (
    <div className="app">
      <header className="header">
        <h1>🎙️ Podcast EN → VI Dubbing</h1>
        <p className="subtitle">Chuyển đổi podcast tiếng Anh sang tiếng Việt tự động</p>
      </header>

      <div className="main-layout">
        {/* Left panel: Upload + History */}
        <aside className="sidebar">
          <form onSubmit={handleSubmit} className="upload-form">
            <h2>Nguồn Podcast</h2>

            {/* Input type toggle */}
            <div className="input-toggle">
              <button
                type="button"
                className={`toggle-btn ${inputType === 'file' ? 'active' : ''}`}
                onClick={() => setInputType('file')}
              >
                📁 File
              </button>
              <button
                type="button"
                className={`toggle-btn ${inputType === 'youtube' ? 'active' : ''}`}
                onClick={() => setInputType('youtube')}
              >
                🎬 YouTube
              </button>
              <button
                type="button"
                className={`toggle-btn ${inputType === 'spotify' ? 'active' : ''}`}
                onClick={() => setInputType('spotify')}
              >
                🎵 Spotify
              </button>
            </div>

            {/* File upload */}
            {inputType === 'file' && (
              <div
                className={`drop-zone ${file ? 'has-file' : ''}`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => { e.preventDefault(); setFile(e.dataTransfer.files[0]) }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="audio/*"
                  hidden
                  onChange={e => setFile(e.target.files[0])}
                />
                {file ? (
                  <div className="file-info">
                    <span className="file-icon">🎵</span>
                    <span className="file-name">{file.name}</span>
                    <span className="file-size">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                  </div>
                ) : (
                  <div className="drop-placeholder">
                    <span className="drop-icon">📁</span>
                    <span>Kéo thả hoặc click để chọn file MP3/WAV</span>
                  </div>
                )}
              </div>
            )}

            {/* YouTube URL input */}
            {inputType === 'youtube' && (
              <div className="youtube-input">
                <input
                  type="text"
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={youtubeUrl}
                  onChange={e => setYoutubeUrl(e.target.value)}
                  className="url-input"
                />
                <p className="hint">Hỗ trợ: youtube.com/watch?v=..., youtu.be/...</p>
                <p className="hint warning">⚠️ Nếu lỗi, dùng <a href="https://cobalt.tools" target="_blank" rel="noreferrer">cobalt.tools</a> để tải MP3 rồi upload qua tab "File"</p>
              </div>
            )}

            {/* Spotify URL input */}
            {inputType === 'spotify' && (
              <div className="spotify-input">
                <input
                  type="text"
                  placeholder="https://open.spotify.com/track/... or .../episode/..."
                  value={spotifyUrl}
                  onChange={e => setSpotifyUrl(e.target.value)}
                  className="url-input"
                />
                <p className="hint">Hỗ trợ: track, episode, album, playlist</p>
                <p className="hint info">💡 Sử dụng chất lượng 128kbps để giảm chặn</p>
                <p className="hint warning">⚠️ Nếu lỗi, dùng <a href="https://spotifydown.com" target="_blank" rel="noreferrer">spotifydown.com</a> để tải MP3 rồi upload qua tab "File"</p>
              </div>
            )}

            <div className="form-row">
              <label>
                Giọng đọc
                <select value={speaker} onChange={e => setSpeaker(e.target.value)}>
                  <option value="SF">SF — Nữ miền Nam</option>
                  <option value="NF">NF — Nữ miền Bắc</option>
                  <option value="SM">SM — Nam miền Nam</option>
                  <option value="NM1">NM1 — Nam miền Bắc 1</option>
                  <option value="NM2">NM2 — Nam miền Bắc 2</option>
                </select>
              </label>
              <label>
                Thiết bị
                <select value={device} onChange={e => setDevice(e.target.value)}>
                  <option value="cpu">CPU</option>
                  <option value="cuda">GPU (CUDA)</option>
                </select>
              </label>
            </div>

            <button type="submit" className="btn-primary" disabled={submitting || (inputType === 'file' ? !file : inputType === 'youtube' ? !youtubeUrl.trim() : !spotifyUrl.trim())}>
              {submitting ? '⏳ Đang gửi...' : '▶️ Bắt đầu chuyển đổi'}
            </button>

            {error && <div className="error-msg">{error}</div>}
          </form>

          {/* Job History */}
          <div className="history">
            <h3>Lịch sử Jobs</h3>
            {history.length === 0 && <p className="empty">Chưa có job nào.</p>}
            <ul>
              {history.map(id => (
                <li
                  key={id}
                  className={id === jobId ? 'active' : ''}
                  onClick={() => loadJob(id)}
                >
                  {id.replace('job_', '').replace(/_/g, ' ')}
                </li>
              ))}
            </ul>
          </div>
        </aside>

        {/* Right panel: Job result */}
        <section className="content">
          {!job ? (
            <div className="placeholder-content">
              <div className="placeholder-icon">🎧</div>
              <p>Chọn file podcast hoặc nhập link YouTube và bấm "Bắt đầu chuyển đổi"</p>
              <p className="hint">Pipeline: YouTube/Audio → ASR (Whisper) → Dịch (NLLB-200) → TTS (Valtec)</p>
            </div>
          ) : (
            <div className="job-result">
              <div className="job-header">
                <h2>{job.job_id}</h2>
                <StatusBadge status={job.status} />
              </div>

              {job.status === 'running' && (
                <div className="progress-section">
                  <div className="spinner" />
                  <span className="step-text">{job.step || 'Đang xử lý...'}</span>
                </div>
              )}

              {job.status === 'error' && (
                <div className="error-box">Đã xảy ra lỗi. Kiểm tra logs trên server.</div>
              )}

              {/* Audio player */}
              {audioFile && (
                <div className="audio-section">
                  <h3>🔊 Audio tiếng Việt</h3>
                  <audio controls src={downloadUrl(audioFile)} style={{ width: '100%' }} />
                </div>
              )}

              {/* Texts */}
              {(job.en_text || job.vi_text) && (
                <div className="texts-section">
                  {job.en_text && (
                    <div className="text-block">
                      <h3>📝 Transcript (English)</h3>
                      <pre>{job.en_text}</pre>
                    </div>
                  )}
                  {job.vi_text && (
                    <div className="text-block">
                      <h3>📝 Bản dịch (Tiếng Việt)</h3>
                      <pre>{job.vi_text}</pre>
                    </div>
                  )}
                </div>
              )}

              {/* Download files */}
              {job.files?.length > 0 && (
                <div className="files-section">
                  <h3>📂 Files</h3>
                  <ul className="file-list">
                    {job.files.map(f => (
                      <li key={f}>
                        <a href={downloadUrl(f)} target="_blank" rel="noreferrer">{f}</a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function StatusBadge({ status }) {
  const map = {
    running: { label: 'Đang xử lý', cls: 'badge-running' },
    done: { label: 'Hoàn tất', cls: 'badge-done' },
    error: { label: 'Lỗi', cls: 'badge-error' },
  }
  const s = map[status] || { label: status, cls: '' }
  return <span className={`badge ${s.cls}`}>{s.label}</span>
}

export default App
