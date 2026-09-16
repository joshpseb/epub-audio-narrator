import type { Dispatch, SetStateAction } from "react";
import VoicePicker from "../VoicePicker";
import type { BookManifest, BookStructure, JobState, SynthesizeRequest, Volume } from "../../lib/types";
import { fmtSec, type ScopeMode } from "./utils";

type Props = {
  manifest: BookManifest;
  structure: BookStructure;
  payload: SynthesizeRequest;
  setPayload: Dispatch<SetStateAction<SynthesizeRequest>>;
  scope: ScopeMode;
  setScope: (scope: ScopeMode) => void;
  batchSize: number;
  setBatchSize: (n: number) => void;
  rangeStart: number;
  setRangeStart: (n: number) => void;
  rangeEnd: number;
  setRangeEnd: (n: number) => void;
  selectedVolume: number;
  setSelectedVolume: (n: number) => void;
  computedSelectionLength: number;
  estCost: string;
  pricePerMillion: number;
  busy: boolean;
  job: JobState | null;
  progressPercent: number;
  elapsedSec: number;
  etaSec: number | null;
  synthesizedCount: number;
  totalCount: number;
  showsVolumes: boolean;
  detectedVolumes: Volume[];
  onSynthesize: () => void;
  onRefreshTitles: () => void;
  onOpenReader: () => void;
  updateVoice: (language: string, voiceName: string) => void;
  ttsProvider: "kokoro" | "google";
  setTtsProvider: (provider: "kokoro" | "google") => void;
};

export default function SynthesisPanel({
  manifest,
  structure,
  payload,
  setPayload,
  scope,
  setScope,
  batchSize,
  setBatchSize,
  rangeStart,
  setRangeStart,
  rangeEnd,
  setRangeEnd,
  selectedVolume,
  setSelectedVolume,
  computedSelectionLength,
  estCost,
  pricePerMillion,
  busy,
  job,
  progressPercent,
  elapsedSec,
  etaSec,
  synthesizedCount,
  totalCount,
  showsVolumes,
  detectedVolumes,
  onSynthesize,
  onRefreshTitles,
  onOpenReader,
  updateVoice,
  ttsProvider,
  setTtsProvider,
}: Props) {
  return (
    <section className="card">
      <div className="book-header">
        <div className="book-header-text">
          <h2>{manifest.title}</h2>
          {manifest.authors.length > 0 && <p className="book-authors">{manifest.authors.join(", ")}</p>}
          <div className="stat-pills">
            <span className="stat-pill">{totalCount} chapters</span>
            {synthesizedCount > 0 && (
              <span className="stat-pill stat-pill-green">{synthesizedCount} synthesized</span>
            )}
            {showsVolumes && <span className="stat-pill stat-pill-blue">{detectedVolumes.length} books</span>}
          </div>
        </div>
      </div>

      <p className="section-label">TTS engine</p>
      <div className="scope-row" style={{ marginBottom: "0.8rem" }}>
        <label>
          Engine
          <select value={ttsProvider} onChange={(e) => setTtsProvider(e.target.value as "kokoro" | "google")}>
            <option value="kokoro">Kokoro (local, free)</option>
            <option value="google">Google Cloud TTS</option>
          </select>
        </label>
      </div>

      <p className="section-label">Narrator voice</p>
      <div className="voice-grid">
        {(["en"] as const).map((lang) => {
          const cfg = payload.voices.find((v) => v.language === lang);
          return (
            <VoicePicker
              key={lang}
              language={lang}
              currentVoiceName={cfg?.voice_name ?? ""}
              provider={ttsProvider}
              speakingRate={cfg?.speaking_rate ?? 1}
              pitch={cfg?.pitch ?? 0}
              onChange={(name) => updateVoice(lang, name)}
            />
          );
        })}
      </div>

      <p className="section-label">Synthesis scope</p>
      <div className="scope-row">
        <label>
          Mode
          <select value={scope} onChange={(e) => setScope(e.target.value as ScopeMode)}>
            <option value="next_n">Next N chapters</option>
            <option value="range">Chapter range</option>
            <option value="volume">By book/volume</option>
            <option value="all">Whole book</option>
          </select>
        </label>
        {scope === "next_n" && (
          <label>
            Count
            <input
              type="number"
              min={1}
              value={batchSize}
              onChange={(e) => setBatchSize(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
        )}
        {scope === "range" && (
          <>
            <label>
              From #
              <input
                type="number"
                min={1}
                max={totalCount}
                value={rangeStart}
                onChange={(e) => setRangeStart(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <label>
              To #
              <input
                type="number"
                min={rangeStart}
                max={totalCount}
                value={rangeEnd}
                onChange={(e) => setRangeEnd(Math.max(rangeStart, Number(e.target.value) || rangeStart))}
              />
            </label>
            <span
              style={{
                fontSize: "0.78rem",
                color: "var(--text-3)",
                alignSelf: "flex-end",
                paddingBottom: "0.45rem",
              }}
            >
              of {totalCount}
            </span>
          </>
        )}
        {scope === "volume" && showsVolumes && (
          <label>
            Volume
            <select value={selectedVolume} onChange={(e) => setSelectedVolume(Number(e.target.value))}>
              {detectedVolumes.map((vol) => {
                const done = vol.chapter_ids.filter((id) => structure.synthesized_chapter_ids.includes(id)).length;
                return (
                  <option key={vol.index} value={vol.index}>
                    {vol.name} ({done}/{vol.chapter_ids.length})
                  </option>
                );
              })}
            </select>
          </label>
        )}
        {scope === "volume" && !showsVolumes && (
          <span
            style={{
              fontSize: "0.78rem",
              color: "var(--text-3)",
              alignSelf: "flex-end",
              paddingBottom: "0.45rem",
            }}
          >
            No separate books/volumes detected yet.
          </span>
        )}
      </div>

      <div className="synth-meta">
        <p className="scope-summary">
          {computedSelectionLength} chapter{computedSelectionLength !== 1 ? "s" : ""} selected
          &nbsp;·&nbsp; {pricePerMillion === 0 ? "Local synthesis (free)" : `~$${estCost} estimated cost ($${pricePerMillion}/M chars)`}
        </p>
        <label className="incremental-label">
          <input
            type="checkbox"
            checked={payload.incremental}
            onChange={(e) => setPayload((p) => ({ ...p, incremental: e.target.checked }))}
          />
          Skip already done
        </label>
      </div>

      <button
        className="btn btn-primary btn-full"
        disabled={busy || computedSelectionLength === 0}
        onClick={onSynthesize}
      >
        Synthesize {computedSelectionLength} chapter{computedSelectionLength !== 1 ? "s" : ""}
      </button>

      {job && (
        <div className="synth-status">
          <div className="synth-status-row">
            <span className="synth-status-label">
              {job.status === "running" ? `${job.message} (${progressPercent}%)` : job.message}
            </span>
            {job.status === "running" && (
              <span className="synth-status-time">
                {fmtSec(elapsedSec)}
                {etaSec != null ? ` · ETA ${fmtSec(etaSec)}` : ""}
              </span>
            )}
          </div>
          <div className="synth-progress">
            <div className="synth-progress-fill" style={{ width: `${progressPercent}%` }} />
          </div>
          {job.error && <p className="synth-error">{job.error}</p>}
        </div>
      )}

      <details className="settings-group" style={{ marginTop: "0.85rem" }}>
        <summary>Advanced cleanup settings</summary>
        <div className="settings-group-body">
          <div className="settings-number-row">
            <span>Min. words per chapter</span>
            <input
              type="number"
              value={payload.cleanup.min_chapter_words}
              onChange={(e) =>
                setPayload((p) => ({
                  ...p,
                  cleanup: { ...p.cleanup, min_chapter_words: Number(e.target.value) || 0 },
                }))
              }
            />
          </div>
          {(
            [
              ["drop_toc", "Drop table of contents"],
              ["drop_frontmatter", "Drop frontmatter"],
              ["drop_backmatter", "Drop backmatter"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={payload.cleanup[key]}
                onChange={(e) =>
                  setPayload((p) => ({ ...p, cleanup: { ...p.cleanup, [key]: e.target.checked } }))
                }
              />
              {label}
            </label>
          ))}
          <button className="btn btn-sm btn-secondary" disabled={busy} onClick={onRefreshTitles}>
            {busy ? "Refreshing..." : "Refresh Titles"}
          </button>
        </div>
      </details>

      <div className="book-actions">
        <button className="btn btn-primary btn-full book-actions-primary" onClick={onOpenReader}>
          Open Reader
        </button>
      </div>
    </section>
  );
}
