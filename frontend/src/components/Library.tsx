import { useEffect, useMemo, useState } from "react";
import type { DragEvent } from "react";
import {
  deleteBook,
  exportM4bUrl,
  fetchBookManifest,
  fetchBookStructure,
  listBooks,
  refreshBookTitles,
  startSynthesis,
  uploadBook,
  watchSynthesisProgress,
} from "../lib/api";
import type { BookManifest, BookStructure, JobState, SynthesizeRequest } from "../lib/types";
import DeleteBookModal from "./library/DeleteBookModal";
import DownloadPanel, { type DownloadMode } from "./library/DownloadPanel";
import ImportCard from "./library/ImportCard";
import LibraryListCard from "./library/LibraryListCard";
import SynthesisPanel from "./library/SynthesisPanel";
import ThemeToggle from "./ThemeToggle";
import { perMillionRateForVoiceName, type ScopeMode } from "./library/utils";

type Props = { onOpenBook: (book: BookManifest) => void };

const VOICE_STORAGE_KEY = "epub-audio-narrator.voice-selection.v2";
const defaultVoices: SynthesizeRequest["voices"] = [
  { language: "en", voice_name: "af_heart", speaking_rate: 1, pitch: 0 },
];

function loadSavedVoices(): SynthesizeRequest["voices"] {
  try {
    const raw = localStorage.getItem(VOICE_STORAGE_KEY);
    if (!raw) return defaultVoices;
    const parsed = JSON.parse(raw) as SynthesizeRequest["voices"];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : defaultVoices;
  } catch {
    return defaultVoices;
  }
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error. Please try again.";
}

export default function Library({ onOpenBook }: Props) {
  const [books, setBooks] = useState<Array<{ book_id: string; title: string; authors: string[] }>>([]);
  const [activeFile, setActiveFile] = useState<File | null>(null);
  const [manifest, setManifest] = useState<BookManifest | null>(null);
  const [structure, setStructure] = useState<BookStructure | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [payload, setPayload] = useState<SynthesizeRequest>(() => ({
    cleanup: { min_chapter_words: 20, drop_frontmatter: true, drop_backmatter: true, drop_toc: true },
    voices: loadSavedVoices(),
    selection: { chapter_ids: [] },
    incremental: true,
    tts_provider: "kokoro",
  }));
  const [ttsProvider, setTtsProvider] = useState<"kokoro" | "google">("kokoro");
  const [busy, setBusy] = useState(false);
  const [scope, setScope] = useState<ScopeMode>("next_n");
  const [rangeStart, setRangeStart] = useState(1);
  const [rangeEnd, setRangeEnd] = useState(50);
  const [batchSize, setBatchSize] = useState(20);
  const [selectedVolume, setSelectedVolume] = useState(0);
  const [downloadMode, setDownloadMode] = useState<DownloadMode>("whole");
  const [downloadVolume, setDownloadVolume] = useState(0);
  const [downloadRangeStart, setDownloadRangeStart] = useState(1);
  const [downloadRangeEnd, setDownloadRangeEnd] = useState(1);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeKind, setNoticeKind] = useState<"success" | "error">("success");
  const [synthStartAt, setSynthStartAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<{ bookId: string; label: string } | null>(null);

  useEffect(() => {
    listBooks()
      .then(setBooks)
      .catch((err) => {
        console.error(err);
        setNoticeKind("error");
        setNotice(`Could not load library: ${getErrorMessage(err)}`);
      });
  }, []);
  useEffect(() => {
    localStorage.setItem(VOICE_STORAGE_KEY, JSON.stringify(payload.voices));
  }, [payload.voices]);
  useEffect(() => {
    if (job?.status !== "running" || synthStartAt == null) return;
    const id = window.setInterval(
      () => setElapsedSec(Math.max(0, Math.floor((Date.now() - synthStartAt) / 1000))),
      1000,
    );
    return () => window.clearInterval(id);
  }, [job?.status, synthStartAt]);

  async function loadStructure(bookId: string) {
    const result = await fetchBookStructure(bookId);
    setStructure(result);
    if (result.volumes.length > 0) {
      const rem = result.volumes.find((v) => v.chapter_ids.some((id) => !result.synthesized_chapter_ids.includes(id)));
      setSelectedVolume(rem?.index ?? 0);
      const firstWithAudio = result.volumes.find((v) =>
        v.chapter_ids.some((id) => result.synthesized_chapter_ids.includes(id)),
      );
      setDownloadVolume(firstWithAudio?.index ?? result.volumes[0].index);
    }
    const total = result.chapter_summaries.length;
    const synthIds = new Set(result.synthesized_chapter_ids);
    let dlStart = 1;
    let dlEnd = Math.max(1, total);
    if (synthIds.size > 0) {
      let first = -1;
      let last = -1;
      result.chapter_summaries.forEach((ch, idx) => {
        if (!synthIds.has(ch.id)) return;
        if (first < 0) first = idx;
        last = idx;
      });
      if (first >= 0) {
        dlStart = first + 1;
        dlEnd = last + 1;
      }
    }
    setDownloadRangeStart(dlStart);
    setDownloadRangeEnd(dlEnd);
    setRangeStart(Math.min(result.synthesized_chapter_ids.length + 1 || 1, total));
    setRangeEnd(Math.min(total, (result.synthesized_chapter_ids.length || 0) + 50));
  }

  const computedSelection = useMemo<string[]>(() => {
    if (!structure) return [];
    const all = structure.chapter_summaries.map((c) => c.id);
    const synthesized = new Set(structure.synthesized_chapter_ids);
    const remaining = all.filter((id) => !synthesized.has(id));

    if (scope === "all") return all;
    if (scope === "next_n") return remaining.slice(0, Math.max(1, batchSize));
    if (scope === "volume") return structure.volumes.find((v) => v.index === selectedVolume)?.chapter_ids ?? all;
    if (scope === "range") {
      // Use plain 1-indexed positions — avoids ambiguity when chapter numbers
      // repeat across volumes or when titles don't contain a chapter number.
      const si = Math.max(0, rangeStart - 1);
      const ei = Math.min(all.length - 1, rangeEnd - 1);
      return all.slice(si, Math.max(si, ei) + 1);
    }
    return all;
  }, [scope, structure, batchSize, selectedVolume, rangeStart, rangeEnd]);

  async function handleUpload() {
    if (!activeFile) return;
    setBusy(true);
    try {
      const uploaded = await uploadBook(activeFile);
      setManifest(uploaded);
      await loadStructure(uploaded.book_id);
      setBooks((prev) => [{ book_id: uploaded.book_id, title: uploaded.title, authors: uploaded.authors }, ...prev]);
      setNoticeKind("success");
      setNotice(`Imported "${uploaded.title}".`);
      setTimeout(() => setNotice(null), 4000);
    } catch (error) {
      setNoticeKind("error");
      setNotice(`Import failed: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally { setBusy(false); }
  }

  async function handleOpenExisting(bookId: string) {
    setBusy(true);
    try {
      const book = await fetchBookManifest(bookId);
      setManifest(book);
      await loadStructure(bookId);
      setJob(null);
    } catch (error) {
      setNoticeKind("error");
      setNotice(`Open failed: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally { setBusy(false); }
  }

  async function handleReadBook(bookId: string) {
    setBusy(true);
    try {
      const fetched = await fetchBookManifest(bookId);
      onOpenBook(fetched);
    } catch (error) {
      setNoticeKind("error");
      setNotice(`Could not open reader: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally {
      setBusy(false);
    }
  }

  function handleGoHome() {
    setActiveFile(null);
    setManifest(null);
    setStructure(null);
    setJob(null);
  }

  function handleFileDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".epub")) {
      setNoticeKind("error");
      setNotice("Please drop a valid .epub file.");
      setTimeout(() => setNotice(null), 4000);
      return;
    }
    setActiveFile(file);
  }

  function handleRequestDelete(bookId: string) {
    const target = books.find((b) => b.book_id === bookId);
    setDeleteTarget({ bookId, label: target?.title ?? bookId });
  }

  async function handleConfirmDelete() {
    if (!deleteTarget) return;
    const { bookId, label } = deleteTarget;
    setBusy(true);
    try {
      await deleteBook(bookId);
      setBooks((prev) => prev.filter((b) => b.book_id !== bookId));
      if (manifest?.book_id === bookId) {
        setManifest(null);
        setStructure(null);
        setJob(null);
      }
      setDeleteTarget(null);
      setNoticeKind("success");
      setNotice(`"${label}" deleted.`);
      setTimeout(() => setNotice(null), 4000);
    } catch (error) {
      setNoticeKind("error");
      setNotice(`Delete failed: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshTitles() {
    if (!manifest) return;
    setBusy(true);
    try {
      const result = await refreshBookTitles(manifest.book_id);
      const fresh = await fetchBookManifest(manifest.book_id);
      setManifest(fresh);
      await loadStructure(manifest.book_id);
      const parts = [];
      if (result.manifest_updated) parts.push("chapter titles updated");
      if (result.synthesis_chapters_updated > 0) parts.push(`${result.synthesis_chapters_updated} synthesized titles synced`);
      setNoticeKind("success");
      setNotice(parts.length > 0 ? `Refreshed: ${parts.join(", ")}.` : "Titles are already up to date.");
      setTimeout(() => setNotice(null), 4000);
    } catch (error) {
      setNoticeKind("error");
      setNotice(`Refresh failed: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally {
      setBusy(false);
    }
  }

  async function handleSynthesize() {
    if (!manifest) return;
    setBusy(true);
    try {
      setSynthStartAt(Date.now());
      setElapsedSec(0);
      await startSynthesis(manifest.book_id, {
        ...payload,
        tts_provider: ttsProvider,
        selection: { chapter_ids: computedSelection },
      });
      const stop = watchSynthesisProgress(manifest.book_id, async (state) => {
        setJob(state);
        if (state.status === "done") { stop(); setSynthStartAt(null); if (manifest) await loadStructure(manifest.book_id); }
        else if (state.status === "error") { stop(); setSynthStartAt(null); }
      });
    } catch (error) {
      setSynthStartAt(null);
      setNoticeKind("error");
      setNotice(`Synthesis failed to start: ${getErrorMessage(error)}`);
      setTimeout(() => setNotice(null), 5000);
    } finally { setBusy(false); }
  }

  function updateVoice(language: string, voiceName: string) {
    setPayload((prev) => ({
      ...prev,
      voices: prev.voices.some((v) => v.language === language)
        ? prev.voices.map((v) => v.language === language ? { ...v, voice_name: voiceName } : v)
        : [...prev.voices, { language, voice_name: voiceName, speaking_rate: 1, pitch: 0 }],
    }));
  }

  const synthesizedCount = structure?.synthesized_chapter_ids.length ?? 0;
  const totalCount = structure?.chapter_summaries.length ?? 0;
  const detectedVolumes = structure?.volumes ?? [];
  const showsVolumes = detectedVolumes.length > 1;
  const progressPercent = Math.min(100, Math.max(0, Math.round((job?.progress ?? 0) * 100)));
  const etaSec = job?.status === "running" && (job.progress ?? 0) > 0
    ? Math.max(0, Math.round((elapsedSec / (job.progress || 1)) - elapsedSec)) : null;
  const selectedEnVoice = payload.voices.find((voice) => voice.language === "en")?.voice_name;
  const pricePerMillion = perMillionRateForVoiceName(selectedEnVoice, ttsProvider);
  const estCost = (
    (computedSelection.reduce((acc, id) => {
      const ch = structure?.chapter_summaries.find((c) => c.id === id);
      return acc + (ch?.word_count ?? 0) * 6;
    }, 0) / 1_000_000) * pricePerMillion
  ).toFixed(2);
  const synthesizedInRange = useMemo(() => {
    if (!structure || totalCount === 0) return 0;
    const si = Math.max(0, downloadRangeStart - 1);
    const ei = Math.min(totalCount - 1, downloadRangeEnd - 1);
    if (si > ei || downloadRangeStart > downloadRangeEnd) return 0;
    const synth = new Set(structure.synthesized_chapter_ids);
    return structure.chapter_summaries.slice(si, ei + 1).filter((ch) => synth.has(ch.id)).length;
  }, [structure, totalCount, downloadRangeStart, downloadRangeEnd]);
  const downloadableVolumes = detectedVolumes.filter((vol) =>
    vol.chapter_ids.some((id) => structure?.synthesized_chapter_ids.includes(id)),
  );
  const canDownloadVolume = downloadableVolumes.some((vol) => vol.index === downloadVolume);
  const hasVolumeDownloadTargets = downloadableVolumes.length > 0;
  const downloadHref: string | undefined = manifest
    ? downloadMode === "whole"
      ? exportM4bUrl(manifest.book_id)
      : downloadMode === "range"
        ? synthesizedInRange > 0 && downloadRangeStart <= downloadRangeEnd && totalCount > 0
          ? exportM4bUrl(manifest.book_id, {
              startIndex: downloadRangeStart,
              endIndex: downloadRangeEnd,
            })
          : undefined
        : hasVolumeDownloadTargets
          ? exportM4bUrl(manifest.book_id, { volumeIndex: downloadVolume })
          : undefined
    : undefined;

  return (
    <div>
      <header className="app-header">
        <div className="app-header-titleblock">
          <span className="app-header-logo">EPUB Narrator</span>
          <span className="app-header-sub">EPUB → Audiobook</span>
        </div>
        <div className="app-header-actions">
          <ThemeToggle variant="header" />
          {manifest && (
            <button className="btn btn-sm btn-secondary app-header-home" onClick={handleGoHome}>
              Home
            </button>
          )}
        </div>
      </header>

      <div className="page-content">
        {notice && <div className={`toast ${noticeKind === "error" ? "toast-error" : "toast-success"}`}>{notice}</div>}

        {/* ── Upload ── */}
        {!manifest && (
          <ImportCard
            activeFile={activeFile}
            busy={busy}
            onDrop={handleFileDrop}
            onPickFile={setActiveFile}
            onUpload={handleUpload}
          />
        )}

        {/* ── Active book ── */}
        {manifest && structure && (
          <SynthesisPanel
            manifest={manifest}
            structure={structure}
            payload={payload}
            setPayload={setPayload}
            scope={scope}
            setScope={setScope}
            batchSize={batchSize}
            setBatchSize={setBatchSize}
            rangeStart={rangeStart}
            setRangeStart={setRangeStart}
            rangeEnd={rangeEnd}
            setRangeEnd={setRangeEnd}
            selectedVolume={selectedVolume}
            setSelectedVolume={setSelectedVolume}
            computedSelectionLength={computedSelection.length}
            estCost={estCost}
            pricePerMillion={pricePerMillion}
            busy={busy}
            job={job}
            progressPercent={progressPercent}
            elapsedSec={elapsedSec}
            etaSec={etaSec}
            synthesizedCount={synthesizedCount}
            totalCount={totalCount}
            showsVolumes={showsVolumes}
            detectedVolumes={detectedVolumes}
            onSynthesize={handleSynthesize}
            onRefreshTitles={handleRefreshTitles}
            onOpenReader={() => onOpenBook(manifest)}
            updateVoice={updateVoice}
            ttsProvider={ttsProvider}
            setTtsProvider={setTtsProvider}
          />
        )}

        {manifest && synthesizedCount > 0 && structure && (
          <DownloadPanel
            manifest={manifest}
            structure={structure}
            synthesizedCount={synthesizedCount}
            totalChapters={totalCount}
            downloadMode={downloadMode}
            setDownloadMode={setDownloadMode}
            downloadVolume={downloadVolume}
            setDownloadVolume={setDownloadVolume}
            downloadRangeStart={downloadRangeStart}
            setDownloadRangeStart={setDownloadRangeStart}
            downloadRangeEnd={downloadRangeEnd}
            setDownloadRangeEnd={setDownloadRangeEnd}
            synthesizedInRange={synthesizedInRange}
            downloadHref={downloadHref}
            canDownloadVolume={canDownloadVolume}
            hasVolumeDownloadTargets={hasVolumeDownloadTargets}
            detectedVolumes={detectedVolumes}
            downloadableVolumes={downloadableVolumes}
          />
        )}

        {/* ── Library ── */}
        <LibraryListCard
          books={books}
          activeBookId={manifest?.book_id ?? null}
          busy={busy}
          onOpen={handleOpenExisting}
          onRead={handleReadBook}
          onDelete={handleRequestDelete}
        />
      </div>

      {deleteTarget && (
        <DeleteBookModal
          bookTitle={deleteTarget.label}
          busy={busy}
          onDismiss={() => {
            if (!busy) setDeleteTarget(null);
          }}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  );
}
