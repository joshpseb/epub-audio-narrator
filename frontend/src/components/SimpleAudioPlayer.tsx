import { useEffect, useState } from "react";
import type { RefObject } from "react";

const PREVIEW_SPEEDS = [0.75, 1, 1.25, 1.5, 2, 3, 4] as const;

type Props = {
  audioRef: RefObject<HTMLAudioElement>;
};

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  const pad = (n: number) => n.toString().padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  return `${m}:${pad(s)}`;
}

export default function SimpleAudioPlayer({ audioRef }: Props) {
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState<number>(1);

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
  }, [audioRef]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) audio.playbackRate = rate;
  }, [audioRef, rate]);

  const dur = Number.isFinite(duration) && duration > 0 ? duration : 0;
  const scrubMax = dur > 0 ? dur : 1;
  const scrubValue = Math.min(scrubMax, Math.max(0, currentTime));

  return (
    <div className="simple-player">
      <audio
        ref={audioRef}
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
        onTimeUpdate={(e) => setCurrentTime((e.target as HTMLAudioElement).currentTime)}
      />
      <div className="simple-player-row simple-player-scrub">
        <input
          type="range"
          className="player-scrub"
          min={0}
          max={scrubMax}
          step={0.1}
          value={scrubValue}
          aria-label="Seek preview"
          onChange={(e) => {
            const audio = audioRef.current;
            if (!audio) return;
            const v = Number(e.target.value);
            audio.currentTime = v;
            setCurrentTime(v);
          }}
        />
        <span className="player-time">
          {formatClock(currentTime)} / {dur > 0 ? formatClock(dur) : "—:—"}
        </span>
      </div>
      <div className="simple-player-row simple-player-actions">
        <button
          type="button"
          className="player-btn player-btn-play simple-player-play"
          title={playing ? "Pause" : "Play"}
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
        <label className="player-select-label simple-player-speed">
          <span className="player-select-caption">Speed</span>
          <select
            className="player-select"
            aria-label="Preview playback speed"
            value={String(rate)}
            onChange={(e) => setRate(Number(e.target.value))}
          >
            {PREVIEW_SPEEDS.map((v) => (
              <option key={v} value={v}>
                {v === 1 ? "1×" : `${v}×`}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
