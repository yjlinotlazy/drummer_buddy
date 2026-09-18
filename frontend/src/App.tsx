import { FormEvent, KeyboardEvent, lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

const ScoreView = lazy(() => import("./ScoreView"));

type Song = {
  id: string;
  title: string;
  artist: string;
  source_type: "local" | "youtube";
  youtube_id: string | null;
  original_filename: string | null;
  archived: boolean;
  assets: { drumless: boolean; score: boolean };
  tags: string[];
};

type ApiError = { detail?: string | { message?: string; code?: string; song?: Song } };

type Job = {
  id: string;
  song_id: string;
  job_type: string;
  status: "queued" | "running" | "cancelling" | "cancelled" | "succeeded" | "failed" | "interrupted";
  stage: string;
  progress: number;
  message: string;
  error: string | null;
};

type RecorderStatus = {
  running: boolean;
  path: string | null;
  started_at: string | null;
  exit_code: number | null;
  error: string | null;
  imported_song_id: string | null;
  default_directory: string;
  device: string;
};

type ServerPlayerStatus = {
  state: "idle" | "playing" | "paused" | "error";
  song_id: string | null;
  variant: string | null;
  position: number;
  duration: number;
  loop: boolean;
  error: string | null;
};

type PathSuggestion = { path: string; is_dir: boolean };

type PracticeLog = Record<string, string[]>;

function commonPrefix(values: string[]): string {
  if (values.length === 0) return "";
  let prefix = values[0];
  for (const value of values.slice(1)) {
    while (prefix && !value.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  return prefix;
}

function titleFromPath(path: string): string {
  const filename = path.split(/[\\/]/).pop() || "";
  const stem = filename.replace(/\.[^.]+$/, "");
  return stem.replace(/_+/g, " ").trim().split(/\s+/).filter(Boolean)
    .map((word) => word[0].toLocaleUpperCase() + word.slice(1).toLocaleLowerCase())
    .join(" ");
}

function localDateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function Practice() {
  const today = new Date();
  const [month, setMonth] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [selectedDate, setSelectedDate] = useState(localDateKey(today));
  const [logs, setLogs] = useState<PracticeLog>(() => {
    try { return JSON.parse(localStorage.getItem("drummer-buddy-practice") || "{}"); } catch { return {}; }
  });
  const [loaded, setLoaded] = useState(false);
  const [itemsLoaded, setItemsLoaded] = useState(false);
  const [exercises, setExercises] = useState(() => {
    const defaults = ["lifetime", "paradiddle", "playalong"];
    try {
      const saved = JSON.parse(localStorage.getItem("drummer-buddy-exercises") || "[]");
      return [...defaults, ...saved.filter((item: unknown): item is string => typeof item === "string" && !defaults.includes(item))];
    } catch { return defaults; }
  });

  useEffect(() => {
    void api<PracticeLog>("/api/practice").then((saved) => {
      if (Object.keys(saved).length) setLogs(saved);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);
  useEffect(() => {
    if (!loaded) return;
    localStorage.setItem("drummer-buddy-practice", JSON.stringify(logs));
    void api<PracticeLog>("/api/practice", { method: "PUT", body: JSON.stringify({ logs }) }).catch(() => undefined);
  }, [logs, loaded]);
  useEffect(() => localStorage.setItem("drummer-buddy-exercises", JSON.stringify(exercises.filter((item) => !["lifetime", "paradiddle", "playalong"].includes(item)))), [exercises]);
  useEffect(() => {
    void api<string[]>("/api/practice/items").then((saved) => {
      if (saved.length) setExercises((current) => [...current.filter((item) => ["lifetime", "paradiddle", "playalong"].includes(item)), ...saved]);
      setItemsLoaded(true);
    }).catch(() => setItemsLoaded(true));
  }, []);
  useEffect(() => {
    if (!itemsLoaded) return;
    const custom = exercises.filter((item) => !["lifetime", "paradiddle", "playalong"].includes(item));
    void api<string[]>("/api/practice/items", { method: "PUT", body: JSON.stringify(custom) }).catch(() => undefined);
  }, [exercises, itemsLoaded]);

  const firstDay = new Date(month.getFullYear(), month.getMonth(), 1);
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const offset = (firstDay.getDay() + 6) % 7;
  const cells = Array.from({ length: offset + daysInMonth }, (_, index) => index < offset ? null : index - offset + 1);
  const selected = logs[selectedDate] || [];
  function toggleExercise(name: string) {
    setLogs((current) => {
      const existing = current[selectedDate] || [];
      const next = existing.includes(name) ? existing.filter((item) => item !== name) : [...existing, name];
      return { ...current, [selectedDate]: next };
    });
  }
  function addExercise() {
    const name = window.prompt("练习项目名称");
    if (name?.trim() && !exercises.includes(name.trim())) setExercises((current) => [...current, name.trim()]);
  }

  return (
    <section className="practice-panel">
      <div className="section-heading"><h2>练习</h2><span>练习追踪</span></div>
      <div className="practice-calendar-heading">
        <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} type="button">‹</button>
        <strong>{month.getFullYear()}年{month.getMonth() + 1}月</strong>
        <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} type="button">›</button>
      </div>
      <div className="practice-weekdays">{["一", "二", "三", "四", "五", "六", "日"].map((day) => <span key={day}>{day}</span>)}</div>
      <div className="practice-calendar">
        {cells.map((day, index) => {
          if (!day) return <span className="practice-day empty" key={`empty-${index}`} />;
          const key = localDateKey(new Date(month.getFullYear(), month.getMonth(), day));
          return <button className={`practice-day ${key === selectedDate ? "selected" : ""} ${logs[key]?.length ? "checked" : ""}`} key={key} onClick={() => setSelectedDate(key)} type="button"><b>{day}</b>{logs[key]?.length ? <small>✓</small> : null}</button>;
        })}
      </div>
      <div className="practice-detail">
        <div className="practice-detail-heading"><h3>{selectedDate} 练习记录</h3><button onClick={addExercise} type="button">＋ 添加项目</button></div>
        {exercises.map((exercise) => <label className="practice-item" key={exercise}><input checked={selected.includes(exercise)} onChange={() => toggleExercise(exercise)} type="checkbox" />{exercise}</label>)}
      </div>
    </section>
  );
}

const tempoSections = [
  { name: "Largo", chinese: "广板", min: 30, max: 55, angle: -112 },
  { name: "Adagio", chinese: "慢板", min: 56, max: 75, angle: -67 },
  { name: "Andante", chinese: "行板", min: 76, max: 108, angle: -22 },
  { name: "Moderato", chinese: "中板", min: 109, max: 120, angle: 22 },
  { name: "Allegro", chinese: "快板", min: 121, max: 168, angle: 67 },
  { name: "Presto", chinese: "急板", min: 169, max: 240, angle: 112 },
];

function tempoSection(bpm: number) {
  return tempoSections.find((section) => bpm <= section.max) || tempoSections[tempoSections.length - 1];
}

function TempoKnob({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const knob = useRef<HTMLDivElement | null>(null);
  const update = (event: React.PointerEvent<HTMLDivElement>) => {
    const bounds = knob.current?.getBoundingClientRect();
    if (!bounds) return;
    const radians = Math.atan2(event.clientX - (bounds.left + bounds.width / 2), -(event.clientY - (bounds.top + bounds.height / 2)));
    const angle = Math.max(-135, Math.min(135, radians * 180 / Math.PI));
    onChange(Math.round(30 + ((angle + 135) / 270) * 210));
  };
  const pointerAngle = -135 + ((value - 30) / 210) * 270;
  return <div className="tempo-knob-wrap">
    <div className="tempo-knob" ref={knob} onPointerDown={(event) => { knob.current?.setPointerCapture(event.pointerId); update(event); }} onPointerMove={(event) => { if (event.buttons) update(event); }} role="slider" aria-label="速度旋钮" aria-valuemin={30} aria-valuemax={240} aria-valuenow={value} tabIndex={0}>
      <div className="tempo-knob-mark" style={{ transform: `rotate(${pointerAngle}deg)` }} />
      <span className="tempo-knob-value">{value}</span>
    </div>
    <div className="tempo-labels">{tempoSections.map((section) => <span key={section.name} style={{ transform: `rotate(${section.angle}deg) translateY(-92px) rotate(${-section.angle}deg)` }} className={section.name === tempoSection(value).name ? "active" : ""}>{section.name}<small>{section.chinese}</small></span>)}</div>
  </div>;
}

function Metronome() {
  const [bpm, setBpm] = useState(100);
  const [bpmInput, setBpmInput] = useState("100");
  const [beats, setBeats] = useState(4);
  const [volume, setVolume] = useState(100);
  const [running, setRunning] = useState(false);
  const [beat, setBeat] = useState(-1);
  const [loaded, setLoaded] = useState(false);
  const timer = useRef<number | null>(null);
  const [error, setError] = useState("");
  const section = tempoSection(bpm);
  const changeBpm = (next: number) => {
    const corrected = Math.max(30, Math.min(240, Math.round(next)));
    setBpm(corrected);
    setBpmInput(String(corrected));
  };

  useEffect(() => { void api<{ bpm: number; beats: number; volume: number; running: boolean }>("/api/metronome").then((settings) => { setBpm(settings.bpm); setBpmInput(String(settings.bpm)); setBeats(settings.beats); setVolume(settings.volume); setRunning(settings.running); setLoaded(true); }).catch(() => setLoaded(true)); }, []);
  useEffect(() => { if (loaded) void api("/api/metronome", { method: "PUT", body: JSON.stringify({ bpm, beats, volume }) }).catch(() => undefined); }, [bpm, beats, volume, loaded]);

  useEffect(() => () => { if (timer.current !== null) window.clearInterval(timer.current); }, []);
  async function toggle() {
    if (running) {
      if (timer.current !== null) window.clearInterval(timer.current);
      timer.current = null;
      try { await api("/api/metronome/command", { method: "POST", body: JSON.stringify({ action: "stop" }) }); setRunning(false); setBeat(-1); } catch (caught) { setError(caught instanceof Error ? caught.message : "服务器节拍器停止失败"); }
      return;
    }
    try {
      await api("/api/metronome/command", { method: "POST", body: JSON.stringify({ action: "start", bpm, beats }) });
      setError(""); setBeat(0); setRunning(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "服务器节拍器启动失败"); }
  }
  useEffect(() => {
    if (!running) return;
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => setBeat((current) => (current + 1) % beats), 60000 / bpm);
    return () => { if (timer.current !== null) window.clearInterval(timer.current); };
  }, [bpm, beats, running]);
  return <section className="metronome-panel">
    <div className="section-heading"><h2>节拍器</h2><span>练习工具</span></div>
    <div className="metronome-beats">{Array.from({ length: beats }, (_, index) => <i className={index === beat ? "active" : ""} key={index} />)}</div>
    <TempoKnob value={bpm} onChange={changeBpm} />
    <output className="bpm-display">{bpm}<small>BPM</small></output>
    <p className="tempo-section">{section.name}<small>{section.chinese} · {section.min}–{section.max} BPM</small></p>
    <label className="bpm-control">直接输入 <input className="bpm-number" type="text" inputMode="numeric" value={bpmInput} onChange={(event) => { const next = event.target.value; setBpmInput(next); if (/^\d+$/.test(next)) { const numeric = Number(next); if (numeric >= 30 && numeric <= 240) setBpm(numeric); } }} onBlur={() => changeBpm(Number(bpmInput) || bpm)} /> BPM</label>
    <label className="bpm-control">音量 <input type="range" min="0" max="100" value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
    <div className="metronome-options"><label>拍号 <select value={beats} onChange={(event) => setBeats(Number(event.target.value))}>{[2, 3, 4, 5, 6, 7, 8].map((value) => <option key={value} value={value}>{value}/4</option>)}</select></label><button className="primary" onClick={() => void toggle()}>{running ? "暂停" : "开始"}</button></div>
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiError;
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.message;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function AddSong({ onAdded }: { onAdded: (song: Song) => void }) {
  const [source, setSource] = useState("");
  const [defaultPath, setDefaultPath] = useState("");
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pathFocused, setPathFocused] = useState(false);
  const [suggestions, setSuggestions] = useState<PathSuggestion[]>([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1);
  const autoTitle = useRef("");

  useEffect(() => {
    void api<{ path: string }>("/api/files/default").then(({ path }) => {
      setDefaultPath(path);
      setSource((current) => current || path);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    const inferred = titleFromPath(source);
    setTitle((current) => {
      if (current && current !== autoTitle.current) return current;
      autoTitle.current = inferred;
      return inferred;
    });
  }, [source]);

  useEffect(() => {
    if (!pathFocused) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const matches = await api<PathSuggestion[]>(`/api/files/complete?path=${encodeURIComponent(source)}`);
        if (!cancelled) {
          setSuggestions(matches);
          setSelectedSuggestion(-1);
        }
      } catch {
        if (!cancelled) setSuggestions([]);
      }
    }, 120);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pathFocused, source]);

  function chooseSuggestion(suggestion: PathSuggestion) {
    setSource(suggestion.path);
    setSelectedSuggestion(-1);
    if (!suggestion.is_dir) setSuggestions([]);
  }

  function completePath(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && suggestions.length > 0) {
      event.preventDefault();
      setSelectedSuggestion((current) => Math.min(current + 1, suggestions.length - 1));
      return;
    }
    if (event.key === "ArrowUp" && suggestions.length > 0) {
      event.preventDefault();
      setSelectedSuggestion((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key !== "Tab" || suggestions.length === 0) return;
    event.preventDefault();
    if (selectedSuggestion >= 0 || suggestions.length === 1) {
      chooseSuggestion(suggestions[Math.max(0, selectedSuggestion)]);
      return;
    }
    const shared = commonPrefix(suggestions.map((suggestion) => suggestion.path));
    if (shared !== source && shared.toLocaleLowerCase().startsWith(source.toLocaleLowerCase())) setSource(shared);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = { path: source, title: title || null, artist };
      const song = await api<Song>("/api/songs/local", { method: "POST", body: JSON.stringify(body) });
      onAdded(song);
      setSource(defaultPath);
      setSuggestions([]);
      setTitle("");
      autoTitle.current = "";
      setArtist("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not add song");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="add-panel desktop-only">
      <span className="eyebrow">ADD LOCAL FILE</span>
      <form onSubmit={submit}>
        <label className="path-field">
          Absolute file path
          <input
            required
            value={source}
            onChange={(event) => setSource(event.target.value)}
            onFocus={() => setPathFocused(true)}
            onBlur={() => setPathFocused(false)}
            onKeyDown={completePath}
            placeholder="Type a path and press Tab"
            autoComplete="off"
          />
          <small>Terminal-style completion: Tab completes, ↑/↓ selects.</small>
          {pathFocused && suggestions.length > 0 && (
            <div className="path-suggestions" role="listbox">
              {suggestions.map((suggestion, index) => (
                <button
                  className={index === selectedSuggestion ? "selected" : ""}
                  key={suggestion.path}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => chooseSuggestion(suggestion)}
                  role="option"
                  aria-selected={index === selectedSuggestion}
                  type="button"
                >
                  <span>{suggestion.is_dir ? "DIR" : "FILE"}</span>{suggestion.path}
                </button>
              ))}
            </div>
          )}
        </label>
        <div className="metadata-row">
          <label>Title <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional" /></label>
          <label>Artist <input value={artist} onChange={(event) => setArtist(event.target.value)} placeholder="Optional" /></label>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Adding…" : "Add to library"}</button>
      </form>
    </section>
  );
}

function Recorder({ onAdded }: { onAdded: (song: Song) => void }) {
  const [status, setStatus] = useState<RecorderStatus | null>(null);
  const [directory, setDirectory] = useState("");
  const [songName, setSongName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const next = await api<RecorderStatus>("/api/recorder");
      setStatus(next);
      setDirectory((current) => current || next.default_directory);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load recorder");
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 1000);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  async function recorderAction(action: "start" | "stop") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const body = action === "start" ? JSON.stringify({ directory, song_name: songName }) : undefined;
      setStatus(await api<RecorderStatus>(`/api/recorder/${action}`, { method: "POST", body }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Could not ${action} recording`);
    } finally {
      setBusy(false);
    }
  }

  async function cleanRecorded() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const song = await api<Song>("/api/recorder/clean", { method: "POST" });
      onAdded(song);
      setMessage(`Added ${song.title} to the library.`);
      setSongName("");
      await loadStatus();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not clean recording");
    } finally {
      setBusy(false);
    }
  }

  const canClean = Boolean(status?.path && !status.running && status.exit_code !== null && !status.imported_song_id);
  const filename = songName.toLowerCase().endsWith(".wav") ? songName : `${songName}.wav`;

  return (
    <section className="add-panel recorder-panel desktop-only">
      <div className="recorder-heading">
        <div><span className="eyebrow">SYSTEM AUDIO RECORDER</span><h2>Capture audio playing in another page</h2></div>
        <span className={`recording-state ${status?.running ? "running" : ""}`}>{status?.running ? "● Recording" : "Idle"}</span>
      </div>
      <div className="metadata-row">
        <label>Output directory <input disabled={status?.running || busy} value={directory} onChange={(event) => setDirectory(event.target.value)} /></label>
        <label>Song name <input disabled={status?.running || busy} value={songName} onChange={(event) => setSongName(event.target.value.replace(/\s+/g, "_"))} /></label>
      </div>
      <code className="record-command">arecord -D {status?.device || "pulse"} -f cd {directory}/{filename}</code>
      {status?.path && <p className="record-path">Latest recording: {status.path}</p>}
      {(error || status?.error) && <p className="error" role="alert">{error || status?.error}</p>}
      {message && <p className="success">{message}</p>}
      <div className="recorder-actions">
        <button className="primary" disabled={busy || status?.running || !directory.trim() || !songName.trim()} onClick={() => void recorderAction("start")}>Start recording</button>
        <button disabled={busy || !status?.running} onClick={() => void recorderAction("stop")}>Stop</button>
        <button disabled={busy || !canClean} onClick={() => void cleanRecorded()}>Add to library</button>
      </div>
    </section>
  );
}

function preciseTime(value: number): string {
  if (!Number.isFinite(value)) return "0:00";
  const minutes = Math.floor(value / 60);
  return `${minutes}:${Math.floor(value % 60).toString().padStart(2, "0")}`;
}

function Player({ song, onClose }: { song: Song; onClose: () => void }) {
  const [variant, setVariant] = useState<"original" | "drumless" | null>(song.assets.drumless ? null : "original");
  const [output, setOutput] = useState<"device" | "server">(() => (
    typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches ? "server" : "device"
  ));
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [loopSong, setLoopSong] = useState(false);
  const [volume, setVolume] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [playerError, setPlayerError] = useState("");
  const [outputBusy, setOutputBusy] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const gainNodeRef = useRef<GainNode | null>(null);
  const mediaSourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const serverStart = useRef({ position: 0, autoplay: false });

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audioContextRef.current) audioContextRef.current = new AudioContext();
    if (!mediaSourceRef.current) {
      mediaSourceRef.current = audioContextRef.current.createMediaElementSource(audio);
      gainNodeRef.current = audioContextRef.current.createGain();
      mediaSourceRef.current.connect(gainNodeRef.current).connect(audioContextRef.current.destination);
    }
    gainNodeRef.current!.gain.value = volume;
    void audioContextRef.current.resume().catch(() => undefined);
  }, [variant, volume]);

  useEffect(() => () => {
    void audioContextRef.current?.close();
  }, []);

  const applyServerStatus = useCallback((status: ServerPlayerStatus) => {
    setPosition(status.position);
    if (status.duration > 0) setDuration(status.duration);
    setPlaying(status.state === "playing");
    setLoopSong(status.loop);
    setPlayerError(status.error || "");
    const audio = audioRef.current;
    if (!audio || audio.readyState === 0) return;
    if (Math.abs(audio.currentTime - status.position) > .35) audio.currentTime = status.position;
    if (status.state === "playing" && audio.paused) void audio.play();
    if (status.state !== "playing" && !audio.paused) audio.pause();
  }, []);

  useEffect(() => {
    if (output !== "server" || variant === null) return;
    let cancelled = false;
    let timer = 0;
    const start = async () => {
      try {
        const status = await api<ServerPlayerStatus>("/api/player/load", {
          method: "POST",
          body: JSON.stringify({
            song_id: song.id,
            variant,
            position: serverStart.current.position,
            autoplay: serverStart.current.autoplay,
            loop: loopSong,
          }),
        });
        if (cancelled) return;
        applyServerStatus(status);
        timer = window.setInterval(async () => {
          try {
            const current = await api<ServerPlayerStatus>("/api/player");
            if (!cancelled) applyServerStatus(current);
          } catch (caught) {
            if (!cancelled) setPlayerError(caught instanceof Error ? caught.message : "Could not read server player");
          }
        }, 400);
      } catch (caught) {
        if (!cancelled) setPlayerError(caught instanceof Error ? caught.message : "Could not start server playback");
      }
    };
    void start();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      void fetch("/api/player/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop" }),
        keepalive: true,
      });
    };
  }, [applyServerStatus, output, song.id, variant]);

  async function switchOutput(next: "device" | "server") {
    if (next === output || outputBusy) return;
    setOutputBusy(true);
    setPlayerError("");
    const audio = audioRef.current;
    try {
      if (next === "server") {
        serverStart.current = { position: audio?.currentTime || position, autoplay: audio ? !audio.paused : false };
        if (audio) audio.muted = true;
        setOutput("server");
      } else {
        const status = await api<ServerPlayerStatus>("/api/player");
        setOutput("device");
        if (audio) {
          audio.currentTime = status.position;
          audio.muted = false;
          if (status.state === "playing") await audio.play();
          else audio.pause();
        }
      }
    } catch (caught) {
      if (audio) audio.muted = false;
      setPlayerError(caught instanceof Error ? caught.message : "Could not switch output");
    } finally {
      setOutputBusy(false);
    }
  }

  async function sendServerCommand(action: string, value?: number | boolean) {
    try {
      applyServerStatus(await api<ServerPlayerStatus>("/api/player/command", {
        method: "POST",
        body: JSON.stringify({ action, value }),
      }));
    } catch (caught) {
      setPlayerError(caught instanceof Error ? caught.message : "Could not control server player");
    }
  }

  function seekTo(value: number) {
    const audio = audioRef.current;
    if (!audio) return;
    const next = Math.max(0, Math.min(value, Number.isFinite(audio.duration) ? audio.duration : value));
    audio.currentTime = next;
    setPosition(next);
    if (output === "server") void sendServerCommand("seek", next);
  }

  function togglePlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    if (output === "server") {
      void sendServerCommand(playing ? "pause" : "play");
      return;
    }
    if (audio.paused) {
      if (Number.isFinite(audio.duration) && audio.currentTime >= audio.duration) audio.currentTime = 0;
      void audio.play();
    }
    else audio.pause();
  }

  function toggleLoop() {
    const next = !loopSong;
    setLoopSong(next);
    if (audioRef.current) audioRef.current.loop = next;
    if (output === "server") void sendServerCommand("loop", next);
  }

  return (
    <div className="player-shell">
      <div className="player-heading">
        <div><span className="eyebrow">NOW PLAYING</span><h2>{song.title}</h2><p>{song.artist || "Unknown artist"}</p></div>
        <button className="icon-button" onClick={onClose} aria-label="Close player">×</button>
      </div>
      {song.source_type === "local" && (
        <div className="output-switch" aria-label="Audio output">
          <span>Output</span>
          <button className={output === "device" ? "active" : ""} disabled={outputBusy} onClick={() => void switchOutput("device")} type="button">This device</button>
          <button className={output === "server" ? "active" : ""} disabled={outputBusy} onClick={() => void switchOutput("server")} type="button">Server</button>
        </div>
      )}
      {song.source_type === "youtube" ? (
        <p className="empty">Embedded YouTube playback has been retired. Record the system audio, then add it to the library.</p>
      ) : variant === null ? (
        <div className="source-choice">
          <p>Choose audio before playback</p>
          <button className="primary" onClick={() => setVariant("drumless")}>Drumless</button>
          <button onClick={() => setVariant("original")}>Original</button>
        </div>
      ) : (
        <div className="audio-controls">
          <audio
            ref={audioRef}
            className="audio-player"
            loop={loopSong}
            muted={output === "server"}
            src={`/api/songs/${song.id}/media?variant=${variant}`}
            onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
            onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onEmptied={() => { setPosition(0); setDuration(0); }}
          />
          <div className="precision-seek">
            <button className="play-toggle" onClick={togglePlayback} type="button">{playing ? "Pause" : "Play"}</button>
            <button onClick={() => seekTo(position - 1)} type="button">−1s</button>
            <input
              aria-label="Precision playback position"
              min="0"
              max={Math.max(duration, .01)}
              onChange={(event) => seekTo(Number(event.target.value))}
              step="1"
              type="range"
              value={Math.min(position, Math.max(duration, .01))}
            />
            <button onClick={() => seekTo(position + 1)} type="button">+1s</button>
            <button className={loopSong ? "active" : ""} aria-pressed={loopSong} onClick={toggleLoop} type="button">Loop song</button>
            <output>{preciseTime(position)} / {preciseTime(duration)}</output>
          </div>
          <label className="volume-control">Volume
            <input
              aria-label="Playback volume"
              min="0"
              max="2"
              onChange={(event) => {
                const next = Number(event.target.value);
                setVolume(next);
                if (output === "server") void sendServerCommand("volume", next * 100);
              }}
              step="0.01"
              type="range"
              value={volume}
            />
            <span>{Math.round(volume * 100)}%</span>
          </label>
          {playerError && <p className="error" role="alert">{playerError}</p>}
        </div>
      )}
      {song.assets.score && <Suspense fallback={<p className="empty">Loading notation…</p>}><ScoreView songId={song.id} audioRef={audioRef} /></Suspense>}
    </div>
  );
}

function EditSong({ song, onSaved, onClose }: { song: Song; onSaved: (song: Song) => void; onClose: () => void }) {
  const [title, setTitle] = useState(song.title);
  const [artist, setArtist] = useState(song.artist);
  const [tags, setTags] = useState((song.tags || []).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [trimPreview, setTrimPreview] = useState("");

  async function previewTrim() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ url: string }>(`/api/songs/${song.id}/trim-preview`, { method: "POST" });
      setTrimPreview(`${result.url}?v=${Date.now()}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create preview");
    } finally {
      setBusy(false);
    }
  }

  async function commitTrim() {
    setBusy(true);
    setError("");
    try {
      const updated = await api<Song>(`/api/songs/${song.id}/trim-commit`, { method: "POST" });
      setTrimPreview("");
      onSaved(updated);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not apply trim");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const updated = await api<Song>(`/api/songs/${song.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title, artist, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) }),
      });
      onSaved(updated);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save song");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="edit-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="player-heading"><h2 id="edit-title">Edit track</h2><button className="icon-button" onClick={onClose} aria-label="Close editor">×</button></div>
        <form onSubmit={submit}>
          <label>Title <input autoFocus required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label>Artist <input value={artist} onChange={(event) => setArtist(event.target.value)} /></label>
          <label>Tags <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="rock, warmup" /><small>用逗号分隔</small></label>
          {song.source_type === "local" && (
            <div className="trim-editor">
              <h3>Trim silence / 去掉首尾空白</h3>
              <p>Original</p>
              <audio controls src={`/api/songs/${song.id}/media?variant=original`} />
              <button className="primary" disabled={busy} onClick={() => void previewTrim()} type="button">Generate preview</button>
              {trimPreview && (
                <>
                  <p>Preview — the original is still unchanged</p>
                  <audio controls src={trimPreview} />
                  <button className="primary" disabled={busy} onClick={() => void commitTrim()} type="button">Use this trimmed audio</button>
                </>
              )}
            </div>
          )}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </form>
      </section>
    </div>
  );
}

function Tasks() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [log, setLog] = useState<{ id: string; content: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setJobs(await api<Job[]>("/api/jobs"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load tasks");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 1000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function cancel(job: Job) {
    try {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not cancel task");
    }
  }

  async function showLog(job: Job) {
    const response = await fetch(`/api/jobs/${job.id}/log`);
    setLog({ id: job.id, content: response.ok ? await response.text() : "Could not load log." });
  }

  return (
    <section className="library">
      <div className="section-heading"><h2>Tasks</h2><span>{jobs.length} total</span></div>
      {error && <p className="error">{error}</p>}
      {jobs.length === 0 ? <p className="empty">No processing tasks yet.</p> : (
        <div className="task-list">
          {jobs.map((job) => (
            <article className="task-card" key={job.id}>
              <div className="task-copy"><strong>{job.job_type}</strong><small>{job.stage} · {job.message}</small></div>
              <span className={`job-status ${job.status}`}>{job.status}</span>
              <progress max="1" value={job.progress} />
              <div className="task-actions">
                <button onClick={() => void showLog(job)}>Log</button>
                {(job.status === "queued" || job.status === "running") && <button onClick={() => void cancel(job)}>Cancel</button>}
              </div>
              {job.error && <p className="error">{job.error}</p>}
              {log?.id === job.id && <pre>{log.content || "No log output yet."}</pre>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function App() {
  const [songs, setSongs] = useState<Song[]>([]);
  const [mode, setMode] = useState<"library" | "utils" | "tasks" | "practice" | "metronome">("library");
  const [selected, setSelected] = useState<Song | null>(null);
  const [editing, setEditing] = useState<Song | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailed, setDetailed] = useState(false);
  const [tagFilter, setTagFilter] = useState("");

  const loadSongs = useCallback(async () => {
    if (mode === "tasks" || mode === "practice" || mode === "metronome") return;
    setLoading(true);
    setError("");
    try {
      setSongs(await api<Song[]>('/api/songs?archived=false'));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load library");
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => { void loadSongs(); }, [loadSongs]);

  async function generateDrumless(song: Song) {
    try {
      await api<Job>("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ song_id: song.id, job_type: "drumless" }),
      });
      setMode("tasks");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start drumless task");
    }
  }

  async function deleteSong(song: Song) {
    if (!window.confirm(`删除「${song.title}」？此操作不可撤销。`)) return;
    try {
      await api(`/api/songs/${song.id}`, { method: "DELETE" });
      setSongs((current) => current.filter((item) => item.id !== song.id));
      setEditing(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete song");
    }
  }

  return (
    <main>
      <header>
        <div className="brand-mark" aria-hidden="true">DB</div>
        <div><span className="eyebrow">DRUMMER BUDDY</span><h1>Your backing room</h1></div>
        <nav className="header-nav">
          <button className={mode === "library" ? "active" : ""} onClick={() => setMode("library")}>Library</button>
          <button className={mode === "utils" ? "active" : ""} onClick={() => setMode("utils")}>Utils</button>
          <button className={mode === "tasks" ? "active" : ""} onClick={() => setMode("tasks")}>Tasks</button>
          <button className={`practice-nav ${mode === "practice" ? "active" : ""}`} onClick={() => setMode("practice")}>练习</button>
          <button className={`metronome-nav ${mode === "metronome" ? "active" : ""}`} onClick={() => setMode("metronome")}>节拍器</button>
        </nav>
      </header>

      {mode === "library" && <AddSong onAdded={(song) => setSongs((current) => [song, ...current])} />}
      {mode === "utils" && (
        <>
          <Recorder onAdded={(song) => setSongs((current) => [song, ...current])} />
          <section className="library">
            <div className="section-heading"><h2>Edit tracks</h2><span>{songs.length} {songs.length === 1 ? "track" : "tracks"}</span></div>
            {error && <p className="error" role="alert">{error}</p>}
            {loading ? <p className="empty">Loading…</p> : songs.length === 0 ? <p className="empty">No tracks to edit.</p> : (
              <div className="song-grid">
                {songs.map((song, index) => (
                  <article className="song-card" key={song.id}>
                    <button className="song-main" onClick={() => setEditing(song)}>
                      <span className="track-number">{String(index + 1).padStart(2, "0")}</span>
                      <span className={`source-badge ${song.source_type}`}>{song.source_type === "youtube" ? "YT" : "FILE"}</span>
                      <span className="song-copy"><strong>{song.title}</strong><small>{song.artist || song.original_filename || "Unknown artist"}</small><small className="song-extra">{song.tags?.length ? `Tags: ${song.tags.join(", ")}` : "No tags"}</small></span>
                      <span className="play-mark">Edit</span>
                    </button>
                    <button className="delete-action" onClick={() => void deleteSong(song)} type="button">删除</button>
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {mode === "practice" && <Practice />}
      {mode === "metronome" && <Metronome />}

      {mode === "tasks" ? <Tasks /> : mode === "utils" || mode === "practice" ? null : <section className="library">
        <div className="section-heading"><h2>Library</h2><span>{songs.length} {songs.length === 1 ? "track" : "tracks"}</span></div>
        <div className="library-controls"><label>Tag <select value={tagFilter} onChange={(event) => setTagFilter(event.target.value)}><option value="">全部</option>{[...new Set(songs.flatMap((song) => song.tags || []))].sort().map((tag) => <option key={tag} value={tag}>{tag}</option>)}</select></label><button onClick={() => setDetailed((current) => !current)} type="button">{detailed ? "简洁" : "详细"}</button></div>
        {error && <p className="error" role="alert">{error}</p>}
        {loading ? <p className="empty">Loading…</p> : songs.length === 0 ? (
          <p className="empty">Add a YouTube link or local song to start playing.</p>
        ) : (
          <div className="song-grid">
            {songs.filter((song) => !tagFilter || (song.tags || []).includes(tagFilter)).map((song, index) => (
              <div className="song-entry" key={song.id}>
                <article className="song-card">
                  <button
                    className="song-main"
                    aria-expanded={selected?.id === song.id}
                    onClick={() => setSelected((current) => current?.id === song.id ? null : song)}
                  >
                    <span className="track-number">{String(index + 1).padStart(2, "0")}</span>
                    <span className={`source-badge ${song.source_type}`}>{song.source_type === "youtube" ? "YT" : "FILE"}</span>
                    <span className="song-copy"><strong>{song.title}</strong><small>{song.artist || song.original_filename || "Unknown artist"}</small>{detailed && <small className="song-extra">{song.tags?.length ? `Tags: ${song.tags.join(", ")}` : "No tags"} · {song.source_type}</small>}</span>
                    <span className="play-mark">▶</span>
                  </button>
                  {!song.archived && song.source_type === "local" && !song.assets.drumless && (
                    <button className="archive-action desktop-only" onClick={() => void generateDrumless(song)}>Make drumless</button>
                  )}
                </article>
                {selected?.id === song.id && <Player song={selected} onClose={() => setSelected(null)} />}
              </div>
            ))}
          </div>
        )}
      </section>}

      {editing && <EditSong song={editing} onClose={() => setEditing(null)} onSaved={(updated) => {
        setSongs((current) => current.map((song) => song.id === updated.id ? updated : song));
        if (selected?.id === updated.id) setSelected(updated);
        setEditing(null);
      }} />}
    </main>
  );
}

export default App;
