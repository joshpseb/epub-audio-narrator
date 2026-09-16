import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { SentenceTiming } from "../lib/types";

const SPEED_STORAGE_KEY = "epub-audio-narrator.player-speed.v1";
const SPEED_MIN = 0.5;
const SPEED_MAX = 4;
const SPEED_OPTIONS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3, 3.25, 3.5, 3.75, 4] as const;

type Props = {
  src: string;
  chapterId: string;
  currentTime: number;
  audioRef: RefObject<HTMLAudioElement>;
  activeSentence: SentenceTiming | null;
  onTimeUpdate: (time: number) => void;
  onPrevSentence: () => void;
  onNextSentence: () => void;
  onPrevChapter: () => void;
  onNextChapter: () => void;
  onEnded?: () => void;
  onSleepFired?: () => void;
};

function readStoredSpeed(): number {
  try {
    const raw = localStorage.getItem(SPEED_STORAGE_KEY);
    if (!raw) return 1;
    const n = Number(raw);
    if (!Number.isFinite(n)) return 1;
    return snapSpeed(Math.min(SPEED_MAX, Math.max(SPEED_MIN, n)));
  } catch {
    return 1;
  }
}

function snapSpeed(n: number): number {
  let best: number = SPEED_OPTIONS[0];
  let bestD = Math.abs(n - best);
  for (const v of SPEED_OPTIONS) {
    const d = Math.abs(n - v);
    if (d < bestD) {
      best = v;
      bestD = d;
    }
  }
  return best;
}

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  const pad = (n: number) => n.toString().padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  return `${m}:${pad(s)}`;
}

function formatCountdown(ms: number): string {
  const sec = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function Player({
  src,
  chapterId,
  currentTime,
  audioRef,
  activeSentence,
  onTimeUpdate,
  onPrevSentence,
  onNextSentence,
  onPrevChapter,
  onNextChapter,
  onEnded,
  onSleepFired,
}: Props) {
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(() => readStoredSpeed());
  const [sleepDeadline, setSleepDeadline] = useState<number | null>(null);
  const [sleepPreset, setSleepPreset] = useState("");
  const [, setSleepTick] = useState(0);
  const sleepTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSleepTimer = useCallback(() => {
    if (sleepTimeoutRef.current != null) {
      clearTimeout(sleepTimeoutRef.current);
      sleepTimeoutRef.current = null;
    }
    setSleepDeadline(null);
    setSleepPreset("");
  }, []);

  const scheduleSleepMinutes = useCallback(
    (minutes: number) => {
      if (sleepTimeoutRef.current != null) {
        clearTimeout(sleepTimeoutRef.current);
        sleepTimeoutRef.current = null;
      }
      const ms = minutes * 60 * 1000;
      const end = Date.now() + ms;
      setSleepDeadline(end);
      setSleepPreset(String(minutes));
      sleepTimeoutRef.current = setTimeout(() => {
        const audio = audioRef.current;
        if (audio) audio.pause();
        onSleepFired?.();
        sleepTimeoutRef.current = null;
        setSleepDeadline(null);
        setSleepPreset("");
      }, ms);
    },
    [audioRef, onSleepFired],
  );

  useEffect(() => {
    if (sleepDeadline == null) return;
    const id = setInterval(() => setSleepTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [sleepDeadline]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    setPlaying(!audio.paused);
    return () => {
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
    };
  }, [audioRef, src]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) audio.playbackRate = rate;
  }, [audioRef, rate, src]);

  useEffect(() => {
    try {
      localStorage.setItem(SPEED_STORAGE_KEY, String(rate));
    } catch {
      // ignore
    }
  }, [rate]);

  useEffect(() => {
    return () => {
      if (sleepTimeoutRef.current != null) clearTimeout(sleepTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    const keyHandler = (event: KeyboardEvent) => {
      const t = event.target;
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return;
      if (event.key === " ") {
        event.preventDefault();
        const audio = audioRef.current;
        if (!audio) return;
        if (audio.paused) {
          void audio.play();
        } else {
          audio.pause();
        }
      }
      if (event.key.toLowerCase() === "j") onPrevSentence();
      if (event.key.toLowerCase() === "k") onNextSentence();
      if (event.key === "ArrowLeft") onPrevChapter();
      if (event.key === "ArrowRight") onNextChapter();
      if (event.key === "[") {
        setRate((r) => {
          const i = SPEED_OPTIONS.reduce(
            (bestI, v, idx) => (Math.abs(v - r) < Math.abs(SPEED_OPTIONS[bestI] - r) ? idx : bestI),
            0,
          );
          return SPEED_OPTIONS[Math.max(0, i - 1)];
        });
      }
      if (event.key === "]") {
        setRate((r) => {
          const i = SPEED_OPTIONS.reduce(
            (bestI, v, idx) => (Math.abs(v - r) < Math.abs(SPEED_OPTIONS[bestI] - r) ? idx : bestI),
            0,
          );
          return SPEED_OPTIONS[Math.min(SPEED_OPTIONS.length - 1, i + 1)];
        });
      }
    };
    window.addEventListener("keydown", keyHandler);
    return () => window.removeEventListener("keydown", keyHandler);
  }, [audioRef, onNextSentence, onPrevSentence, onPrevChapter, onNextChapter]);

  const dur = Number.isFinite(duration) && duration > 0 ? duration : 0;
  const scrubMax = dur > 0 ? dur : 1;
  const scrubValue = Math.min(scrubMax, Math.max(0, currentTime));

  return (
    <div className="player">
      <audio
        ref={audioRef}
        src={src}
        className="player-audio-hidden"
        aria-hidden
        preload="metadata"
        onLoadedMetadata={(e) => {
          const el = e.target as HTMLAudioElement;
          setDuration(el.duration || 0);
        }}
        onDurationChange={(e) => {
          const el = e.target as HTMLAudioElement;
          if (Number.isFinite(el.duration)) setDuration(el.duration);
        }}
        onTimeUpdate={(e) => onTimeUpdate((e.target as HTMLAudioElement).currentTime)}
        onEnded={onEnded}
      />
      <div className="player-stack">
        <div className="player-scrub-row">
          <input
            type="range"
            className="player-scrub"
            min={0}
            max={scrubMax}
            step={0.25}
            value={scrubValue}
            aria-label="Seek in chapter"
            onChange={(e) => {
              const audio = audioRef.current;
              if (!audio) return;
              const v = Number(e.target.value);
              audio.currentTime = v;
              onTimeUpdate(v);
            }}
          />
          <span className="player-time" aria-live="polite">
            {formatClock(currentTime)} / {dur > 0 ? formatClock(dur) : "—:—"}
          </span>
        </div>
        <p className="player-status">
          {activeSentence ? activeSentence.text.slice(0, 140) : "Not playing"}
        </p>
        <div className="player-controls-row">
          <button
            type="button"
            className="player-btn player-btn-play"
            title={playing ? "Pause (Space)" : "Play (Space)"}
            aria-label={playing ? "Pause" : "Play"}
            onClick={() => {
              const audio = audioRef.current;
              if (!audio) return;
              if (audio.paused) void audio.play();
              else audio.pause();
            }}
          >
            {playing ? "⏸" : "▶"}
          </button>
          <button type="button" className="player-btn" title="Previous chapter (←)" onClick={onPrevChapter}>
            <span className="player-btn-label">
              ⏮<br />
              Ch
            </span>
          </button>
          <button type="button" className="player-btn" title="Previous sentence (J)" onClick={onPrevSentence}>
            <span className="player-btn-label">
              ◀◀<br />
              Sen
            </span>
          </button>
          <button type="button" className="player-btn" title="Next sentence (K)" onClick={onNextSentence}>
            <span className="player-btn-label">
              ▶▶<br />
              Sen
            </span>
          </button>
          <button type="button" className="player-btn" title="Next chapter (→)" onClick={onNextChapter}>
            <span className="player-btn-label">
              ⏭<br />
              Ch
            </span>
          </button>
          <label className="player-select-label">
            <span className="player-select-caption">Speed</span>
            <select
              className="player-select"
              value={String(rate)}
              aria-label="Playback speed"
              onChange={(e) => setRate(Number(e.target.value))}
            >
              {SPEED_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {v === 1 ? "1×" : `${v}×`}
                </option>
              ))}
            </select>
          </label>
          <label className="player-select-label">
            <span className="player-select-caption">Sleep</span>
            <select
              className="player-select"
              aria-label="Sleep timer"
              value={sleepPreset}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "") clearSleepTimer();
                else scheduleSleepMinutes(Number(v));
              }}
            >
              <option value="">Off</option>
              <option value="5">5 min</option>
              <option value="15">15 min</option>
              <option value="30">30 min</option>
              <option value="45">45 min</option>
              <option value="60">60 min</option>
            </select>
          </label>
          {sleepDeadline != null && (
            <span className="player-sleep-remaining" key={sleepDeadline}>
              {formatCountdown(sleepDeadline - Date.now())} left
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
