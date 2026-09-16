import { useState } from "react";
import { downloadM4bExport, suggestedM4bFilename } from "../../lib/api";
import type { BookManifest, BookStructure, Volume } from "../../lib/types";

export type DownloadMode = "whole" | "volume" | "range";

type Props = {
  manifest: BookManifest;
  structure: BookStructure;
  synthesizedCount: number;
  totalChapters: number;
  downloadMode: DownloadMode;
  setDownloadMode: (mode: DownloadMode) => void;
  downloadVolume: number;
  setDownloadVolume: (index: number) => void;
  downloadRangeStart: number;
  setDownloadRangeStart: (n: number) => void;
  downloadRangeEnd: number;
  setDownloadRangeEnd: (n: number) => void;
  synthesizedInRange: number;
  downloadHref: string | undefined;
  canDownloadVolume: boolean;
  hasVolumeDownloadTargets: boolean;
  detectedVolumes: BookStructure["volumes"];
  downloadableVolumes: Volume[];
};

export default function DownloadPanel({
  manifest,
  structure,
  synthesizedCount,
  totalChapters,
  downloadMode,
  setDownloadMode,
  downloadVolume,
  setDownloadVolume,
  downloadRangeStart,
  setDownloadRangeStart,
  downloadRangeEnd,
  setDownloadRangeEnd,
  synthesizedInRange,
  downloadHref,
  canDownloadVolume,
  hasVolumeDownloadTargets,
  detectedVolumes,
  downloadableVolumes,
}: Props) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [downloadProgress, setDownloadProgress] = useState<{ loaded: number; total: number | null } | null>(null);

  const inactive =
    !downloadHref ||
    (downloadMode === "volume" && !canDownloadVolume) ||
    (downloadMode === "range" && synthesizedInRange === 0);

  const vol =
    downloadMode === "volume" ? detectedVolumes.find((v) => v.index === downloadVolume) : undefined;

  const displayTitle =
    downloadMode === "volume" && vol
      ? `${manifest.title} ${vol.name}`.replace(/\s+/g, " ").trim()
      : downloadMode === "range"
        ? `${manifest.title} (Ch ${downloadRangeStart}-${downloadRangeEnd})`.replace(/\s+/g, " ").trim()
        : manifest.title;

  const fallbackFilename = suggestedM4bFilename(displayTitle);

  const rangeSpan =
    downloadRangeEnd >= downloadRangeStart ? downloadRangeEnd - downloadRangeStart + 1 : 0;

  async function handleExportClick() {
    if (!downloadHref || inactive || exporting) return;
    setExporting(true);
    setExportError(null);
    setDownloadProgress(null);
    try {
      await downloadM4bExport(downloadHref, fallbackFilename, (loaded, total) => {
        setDownloadProgress({ loaded, total });
      });
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed. Please try again.");
    } finally {
      setExporting(false);
      setDownloadProgress(null);
    }
  }

  return (
    <section className="card">
      <p className="card-title">Download Audiobook</p>
      <div className="download-controls">
        <label>
          Download mode
          <select
            value={downloadMode}
            disabled={exporting}
            onChange={(e) => setDownloadMode(e.target.value as DownloadMode)}
          >
            <option value="whole">Whole book</option>
            <option value="volume">By book/volume</option>
            <option value="range">Chapter range</option>
          </select>
        </label>
        {downloadMode === "volume" && (
          <label>
            Book/Volume
            <select
              value={downloadVolume}
              disabled={!hasVolumeDownloadTargets || exporting}
              onChange={(e) => setDownloadVolume(Number(e.target.value))}
            >
              {hasVolumeDownloadTargets ? (
                downloadableVolumes.map((vol) => {
                  const done = vol.chapter_ids.filter((id) =>
                    structure.synthesized_chapter_ids.includes(id),
                  ).length;
                  return (
                    <option key={vol.index} value={vol.index}>
                      {vol.name} ({done}/{vol.chapter_ids.length})
                    </option>
                  );
                })
              ) : (
                <option value={0}>No volume exports available</option>
              )}
            </select>
          </label>
        )}
        {downloadMode === "range" && (
          <>
            <label>
              From #
              <input
                type="number"
                min={1}
                max={totalChapters}
                value={downloadRangeStart}
                disabled={exporting || totalChapters === 0}
                onChange={(e) => setDownloadRangeStart(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <label>
              To #
              <input
                type="number"
                min={downloadRangeStart}
                max={totalChapters}
                value={downloadRangeEnd}
                disabled={exporting || totalChapters === 0}
                onChange={(e) =>
                  setDownloadRangeEnd(Math.max(downloadRangeStart, Number(e.target.value) || downloadRangeStart))
                }
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
              of {totalChapters}
            </span>
          </>
        )}
      </div>
      <div className="download-grid">
        <button
          type="button"
          className={`download-btn${inactive || exporting ? " download-btn-disabled" : ""}${
            exporting ? " download-btn-exporting" : ""
          }`}
          disabled={inactive || exporting}
          aria-busy={exporting}
          onClick={() => void handleExportClick()}
        >
          <span className="download-btn-row">
            {exporting && <span className="download-spinner" aria-hidden />}
            <span className="download-btn-name">{displayTitle}</span>
          </span>
          <span className="download-btn-meta">
            {inactive
              ? downloadMode === "volume" && !canDownloadVolume
                ? "No audio in this volume yet"
                : downloadMode === "range" && synthesizedInRange === 0
                  ? "No audio in selected range yet"
                  : "Export unavailable"
              : exporting
                ? downloadProgress?.total != null && downloadProgress.total > 0
                  ? `Downloading… ${Math.min(100, Math.round((100 * downloadProgress.loaded) / downloadProgress.total))}%`
                  : "Preparing export — file will save when ready…"
                : downloadMode === "volume"
                  ? "Tap to export volume · M4B"
                  : downloadMode === "range"
                    ? `${synthesizedInRange} of ${rangeSpan} chapters · tap to export M4B`
                    : `${synthesizedCount} chapters · tap to export M4B`}
          </span>
        </button>
      </div>
      {exportError && <p className="download-export-error">{exportError}</p>}
    </section>
  );
}
