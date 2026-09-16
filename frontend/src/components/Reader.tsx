import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { CSSProperties } from "react";
import {
  chapterAudioUrl,
  downloadM4bExport,
  exportM4bUrl,
  fetchAudioManifestOptional,
  fetchBookStructure,
  suggestedM4bFilename,
} from "../lib/api";
import {
  HIGHLIGHT_PRESETS,
  HIGHLIGHT_PRESET_ORDER,
  loadHighlightPreset,
  READER_HIGHLIGHT_STORAGE_KEY,
  type HighlightPresetId,
} from "../lib/readerHighlight";
import { sanitizeReaderHtml, wrapSentenceSpans } from "../lib/readerDom";
import type {
  BookManifest,
  BookStructure,
  ChapterAudioManifest,
  SentenceTiming,
  SynthesisManifest,
} from "../lib/types";
import { useTheme } from "../theme";
import Player from "./Player";
import ThemeToggle from "./ThemeToggle";

type Props = {
  book: BookManifest;
  onBack: () => void;
};

const READER_STATE_KEY = "epub-audio-narrator.reader-state.v1";
const READER_AUTOPLAY_KEY = "epub-audio-narrator.reader-autoplay.v1";

function HighlightSwatches({
  preset,
  onChange,
  themeMode,
}: {
  preset: HighlightPresetId;
  onChange: (id: HighlightPresetId) => void;
  themeMode: "light" | "dark";
}) {
  const tone = themeMode === "dark" ? "dark" : "light";
  return (
    <div className="reader-highlight-pref" role="group" aria-label="Sentence highlight color">
      <span className="reader-highlight-label">Highlight</span>
      <div className="reader-highlight-swatches">
        {HIGHLIGHT_PRESET_ORDER.map((id) => {
          const { bg } = HIGHLIGHT_PRESETS[id][tone];
          return (
            <button
              key={id}
              type="button"
              className={`reader-highlight-swatch${preset === id ? " reader-highlight-swatch-active" : ""}`}
              title={HIGHLIGHT_PRESETS[id].label}
              aria-label={HIGHLIGHT_PRESETS[id].label}
              aria-pressed={preset === id}
              onClick={() => onChange(id)}
            >
              <span className="reader-highlight-dot" style={{ backgroundColor: bg }} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Last sentence with t_start ≤ time — keeps highlight through gaps and trailing silence after t_end. */
function findSentenceByTime(sentences: SentenceTiming[], time: number): SentenceTiming | null {
  if (!sentences.length || time < sentences[0].t_start) return null;
  let lo = 0;
  let hi = sentences.length - 1;
  let best = 0;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (sentences[mid].t_start <= time) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return sentences[best];
}

function offsetTopWithin(el: HTMLElement, scrollRoot: HTMLElement): number {
  const elRect = el.getBoundingClientRect();
  const rootRect = scrollRoot.getBoundingClientRect();
  return elRect.top - rootRect.top + scrollRoot.scrollTop;
}

function volumeSynthesisTone(synthCount: number, total: number): "none" | "partial" | "full" {
  if (total <= 0) return "none";
  if (synthCount <= 0) return "none";
  if (synthCount >= total) return "full";
  return "partial";
}

export default function Reader({ book, onBack }: Props) {
  const { theme } = useTheme();
  const [structure, setStructure] = useState<BookStructure | null>(null);
  const [structureErr, setStructureErr] = useState<string | null>(null);
  const [manifest, setManifest] = useState<SynthesisManifest | null>(null);
  const [audioManifestReady, setAudioManifestReady] = useState(false);
  const [chapterIdx, setChapterIdx] = useState(0);
  const [activeSentenceId, setActiveSentenceId] = useState<string | null>(null);
  const [highlightPreset, setHighlightPreset] = useState<HighlightPresetId>(loadHighlightPreset);
  const [exportingM4b, setExportingM4b] = useState(false);
  const [exportM4bErr, setExportM4bErr] = useState<string | null>(null);
  const [audioTime, setAudioTime] = useState(0);
  const [autoplayNext, setAutoplayNext] = useState<boolean>(() => {
    return localStorage.getItem(READER_AUTOPLAY_KEY) !== "false";
  });
  const [openVolumeIndices, setOpenVolumeIndices] = useState<Set<number>>(() => new Set([0]));

  const audioRef = useRef<HTMLAudioElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const chapterListRef = useRef<HTMLDivElement>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const shouldAutoplayRef = useRef(false);
  const readerStateHydratedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setAudioManifestReady(false);
    async function load() {
      try {
        setStructureErr(null);
        const [struct, synth] = await Promise.all([
          fetchBookStructure(book.book_id),
          fetchAudioManifestOptional(book.book_id),
        ]);
        if (cancelled) return;
        setStructure(struct);
        setManifest(synth);
      } catch (e) {
        if (!cancelled) setStructureErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setAudioManifestReady(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [book.book_id]);

  useEffect(() => {
    localStorage.setItem(READER_AUTOPLAY_KEY, autoplayNext ? "true" : "false");
  }, [autoplayNext]);

  useEffect(() => {
    try {
      localStorage.setItem(READER_HIGHLIGHT_STORAGE_KEY, highlightPreset);
    } catch {
      // ignore quota / private mode
    }
  }, [highlightPreset]);

  useEffect(() => {
    readerStateHydratedRef.current = false;
    try {
      const raw = localStorage.getItem(READER_STATE_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as { bookId?: string; chapterId?: string; time?: number };
        if (saved.bookId === book.book_id && saved.chapterId) {
          const idx = book.chapters.findIndex((chapter) => chapter.id === saved.chapterId);
          if (idx >= 0) {
            setChapterIdx(idx);
            pendingSeekRef.current = Math.max(0, Number(saved.time) || 0);
          }
        }
      }
    } catch {
      /* ignore malformed */
    }
    readerStateHydratedRef.current = true;
  }, [book.book_id, book.chapters]);

  const synthSet = useMemo(() => {
    const ids = structure?.synthesized_chapter_ids ?? [];
    return new Set(ids);
  }, [structure?.synthesized_chapter_ids]);

  const audioByChapterId = useMemo(() => {
    const entries = manifest?.chapters.map((chapter) => [chapter.chapter_id, chapter]) ?? [];
    return new Map(entries as Iterable<[string, ChapterAudioManifest]>);
  }, [manifest]);

  const currentChapter = book.chapters[chapterIdx];
  const currentAudio = currentChapter ? audioByChapterId.get(currentChapter.id) ?? null : null;

  const readerSurfaceStyle = useMemo((): CSSProperties => {
    const tone = theme === "dark" ? "dark" : "light";
    const { bg, fg } = HIGHLIGHT_PRESETS[highlightPreset][tone];
    return {
      "--reader-highlight-bg": bg,
      "--reader-highlight-fg": fg,
    } as CSSProperties;
  }, [highlightPreset, theme]);

  const sanitizedHtml = useMemo(() => sanitizeReaderHtml(currentChapter?.html ?? ""), [currentChapter?.html]);

  const chapterIndexById = useMemo(
    () => Object.fromEntries(book.chapters.map((ch, ii) => [ch.id, ii])) as Record<string, number>,
    [book.chapters],
  );

  const sentencesKey = currentAudio?.sentences.map((sentence) => sentence.id).join("\n") ?? "";

  useEffect(() => {
    setActiveSentenceId(null);
  }, [chapterIdx]);

  useLayoutEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    if (!sanitizedHtml) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = sanitizedHtml;
    if (currentAudio?.sentences?.length) {
      wrapSentenceSpans(container, currentAudio.sentences);
    }
  }, [chapterIdx, sanitizedHtml, sentencesKey, currentAudio?.sentences]);

  useEffect(() => {
    if (!currentAudio) {
      setActiveSentenceId(null);
      return;
    }
    const sentence = findSentenceByTime(currentAudio.sentences, audioTime);
    const nextId = sentence?.id ?? null;
    setActiveSentenceId(nextId);
  }, [audioTime, currentAudio]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container || !currentAudio?.sentences.length) return;

    container.querySelectorAll(".sentence[data-sid].active").forEach((el) => {
      el.classList.remove("active");
    });

    if (!activeSentenceId) return;

    const selector = `[data-sid="${CSS.escape(activeSentenceId)}"]`;
    try {
      const matches = container.querySelectorAll(selector);
      matches.forEach((el) => el.classList.add("active"));
      matches.item(0)?.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch {
      const matches = container.querySelectorAll(".sentence[data-sid]");
      matches.forEach((el) => {
        if ((el as HTMLElement).dataset.sid === activeSentenceId) {
          el.classList.add("active");
        }
      });
    }
  }, [activeSentenceId, chapterIdx, currentAudio?.sentences.length, sentencesKey]);

  useEffect(() => {
    if (!structure?.volumes.length || !currentChapter) return;
    const vidx = structure.volumes.findIndex(
      (volume) => chapterIdx >= volume.start_index && chapterIdx <= volume.end_index,
    );
    if (vidx >= 0) {
      setOpenVolumeIndices((prev) => new Set(prev).add(vidx));
    }
  }, [chapterIdx, currentChapter?.id, structure]);

  useLayoutEffect(() => {
    const scrollRoot = chapterListRef.current;
    if (!scrollRoot) return;
    const activeBtn = scrollRoot.querySelector<HTMLElement>(".volume-chapter-btn.active");
    if (!activeBtn) return;

    const volumeDetails = activeBtn.closest<HTMLElement>(".volume-details");
    const volumeSummary = volumeDetails?.querySelector<HTMLElement>(".volume-summary");
    const summaryHeight = volumeSummary?.getBoundingClientRect().height ?? 0;
    const inner = scrollRoot.querySelector<HTMLElement>(".chapter-list-scroll-inner");
    const insetTop =
      Number.parseFloat(getComputedStyle(inner ?? scrollRoot).paddingTop) ||
      Number.parseFloat(getComputedStyle(scrollRoot).getPropertyValue("--chapter-list-inset")) ||
      0;

    const activeTop = offsetTopWithin(activeBtn, scrollRoot);
    const volumeTop = volumeDetails ? offsetTopWithin(volumeDetails, scrollRoot) : 0;
    const chapterTarget = activeTop - summaryHeight - insetTop;
    const maxScroll = scrollRoot.scrollHeight - scrollRoot.clientHeight;
    const target = Math.min(maxScroll, Math.max(insetTop, volumeTop - insetTop, chapterTarget));

    scrollRoot.scrollTo({ top: target, behavior: "smooth" });
  }, [chapterIdx, openVolumeIndices]);

  useEffect(() => {
    if (!currentChapter || !readerStateHydratedRef.current) return;
    if (pendingSeekRef.current != null) return;
    try {
      localStorage.setItem(
        READER_STATE_KEY,
        JSON.stringify({
          bookId: book.book_id,
          chapterId: currentChapter.id,
          time: audioRef.current?.currentTime ?? audioTime,
        }),
      );
    } catch {
      /* ignore */
    }
  }, [book.book_id, currentChapter?.id, audioTime]);

  useLayoutEffect(() => {
    if (!audioManifestReady || !currentChapter) return;

    if (!currentAudio) {
      if (pendingSeekRef.current != null) {
        pendingSeekRef.current = null;
        setAudioTime(0);
      }
      return;
    }

    const audio = audioRef.current;
    if (!audio) return;

    if (pendingSeekRef.current != null) {
      audio.currentTime = pendingSeekRef.current;
      pendingSeekRef.current = null;
      setAudioTime(audio.currentTime);
    }
    if (shouldAutoplayRef.current) {
      void audio.play().catch(() => undefined);
      shouldAutoplayRef.current = false;
    }
  }, [audioManifestReady, currentChapter?.id, currentAudio?.chapter_id]);

  const handleContentClick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!currentAudio?.sentences.length || !audioRef.current) return;
      const sentenceEl = (event.target as HTMLElement).closest("[data-sid]");
      if (!sentenceEl || !contentRef.current?.contains(sentenceEl)) return;
      const sid = (sentenceEl as HTMLElement).dataset.sid;
      if (!sid) return;
      const sentence = currentAudio.sentences.find((sentenceItem) => sentenceItem.id === sid);
      if (!sentence) return;
      audioRef.current.currentTime = sentence.t_start;
      void audioRef.current.play();
      setActiveSentenceId(sentence.id);
    },
    [currentAudio],
  );

  const synthesizedCountForExport = structure?.synthesized_chapter_ids.length ?? 0;
  const canExportM4b = synthesizedCountForExport > 0;
  const exportUrl = exportM4bUrl(book.book_id);
  const volumes = structure?.volumes ?? [];
  const maxChapterIdx = Math.max(0, book.chapters.length - 1);

  const activeSentence = currentAudio?.sentences.find((sentenceItem) => sentenceItem.id === activeSentenceId) ?? null;

  if (!structure && !structureErr) {
    return (
      <div className="reader" style={readerSurfaceStyle}>
        <aside className="chapter-sidebar">
          <div className="chapter-sidebar-header">
            <div className="chapter-sidebar-actions">
              <button className="btn btn-sm btn-ghost" onClick={onBack}>
                ← Back
              </button>
              <ThemeToggle variant="inline" />
            </div>
          </div>
        </aside>
        <main className="chapter-content">
          <p style={{ color: "var(--text-3)" }}>Loading…</p>
        </main>
      </div>
    );
  }

  if (structureErr || !structure || !currentChapter) {
    return (
      <div className="reader" style={readerSurfaceStyle}>
        <aside className="chapter-sidebar">
          <div className="chapter-sidebar-header">
            <div className="chapter-sidebar-actions">
              <button className="btn btn-sm btn-ghost" onClick={onBack}>
                ← Back
              </button>
              <ThemeToggle variant="inline" />
            </div>
          </div>
        </aside>
        <main className="chapter-content">
          <p style={{ color: "var(--danger)" }}>
            {structureErr ?? "Could not load this book’s structure."}
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className={`reader ${!currentAudio ? "reader-no-audio-playing" : ""}`} style={readerSurfaceStyle}>
      <aside className="chapter-sidebar">
        <div className="chapter-sidebar-header">
          <span className="chapter-sidebar-title">{book.title}</span>
          <div className="chapter-sidebar-actions">
            <button className="btn btn-sm btn-ghost" onClick={onBack}>
              ← Back
            </button>
            <ThemeToggle variant="inline" />
            <button
              type="button"
              className="btn btn-sm btn-secondary reader-m4b-btn"
              disabled={exportingM4b || !canExportM4b}
              title={canExportM4b ? undefined : "Synthesize at least one chapter to export"}
              aria-busy={exportingM4b}
              onClick={() => {
                void (async () => {
                  setExportingM4b(true);
                  setExportM4bErr(null);
                  try {
                    await downloadM4bExport(exportUrl, suggestedM4bFilename(book.title));
                  } catch (e) {
                    setExportM4bErr(e instanceof Error ? e.message : "Export failed");
                  } finally {
                    setExportingM4b(false);
                  }
                })();
              }}
            >
              {exportingM4b ? (
                <>
                  <span className="download-spinner reader-m4b-spinner" aria-hidden />
                  <span>Export…</span>
                </>
              ) : (
                "↓ M4B"
              )}
            </button>
          </div>
          {exportM4bErr && <p className="reader-export-error">{exportM4bErr}</p>}
          {!currentAudio && (
            <p className="reader-no-audio-hint">
              No audio for this chapter yet — read the formatted text, or synthesize in the Library.
            </p>
          )}
          <label className="incremental-label">
            <input
              type="checkbox"
              checked={autoplayNext}
              disabled={!manifest?.chapters.length}
              onChange={(event) => setAutoplayNext(event.target.checked)}
            />
            Autoplay next chapter
          </label>
          <HighlightSwatches preset={highlightPreset} onChange={setHighlightPreset} themeMode={theme} />
        </div>

        <div ref={chapterListRef} className="chapter-list-scroll chapter-list-volumes">
          <div className="chapter-list-scroll-inner">
          {volumes.map((volume) => {
            const done = volume.chapter_ids.filter((id) => synthSet.has(id)).length;
            const total = volume.chapter_ids.length;
            const toneClass = volumeSynthesisTone(done, total);
            return (
              <details
                key={volume.index}
                className="volume-details"
                open={openVolumeIndices.has(volume.index)}
                onToggle={(event) => {
                  const native = event.currentTarget.open;
                  setOpenVolumeIndices((prev) => {
                    const next = new Set(prev);
                    if (native) next.add(volume.index);
                    else next.delete(volume.index);
                    return next;
                  });
                }}
              >
                <summary className="volume-summary">
                  <span className="volume-chevron" aria-hidden />
                  <span className="volume-summary-title">{volume.name}</span>
                  <span className={`volume-synth-chip volume-synth-${toneClass}`} title={`${done} of ${total} chapters synthesized`}>
                    {toneClass === "full" ? "Audio complete" : toneClass === "partial" ? `${done}/${total}` : "No audio"}
                  </span>
                </summary>
                <ul className="volume-chapter-list">
                  {volume.chapter_ids.map((chapterId) => {
                    const chIndex = chapterIndexById[chapterId];
                    if (typeof chIndex !== "number") return null;
                    const summaryTitle = book.chapters[chIndex]?.title ?? chapterId;
                    const synthesized = synthSet.has(chapterId);
                    const isActive = chIndex === chapterIdx;
                    return (
                      <li key={chapterId}>
                        <button
                          type="button"
                          className={isActive ? "volume-chapter-btn active" : "volume-chapter-btn"}
                          onClick={() => {
                            setChapterIdx(chIndex);
                            setOpenVolumeIndices((prev) => new Set(prev).add(volume.index));
                          }}
                        >
                          <span
                            className={`chapter-audio-dot${synthesized ? " chapter-audio-dot-on" : ""}`}
                            title={synthesized ? "Chapter has audio" : "Not synthesized"}
                            aria-hidden
                          />
                          <span className="volume-chapter-title">{summaryTitle}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </details>
            );
          })}
          </div>
        </div>
      </aside>

      <main className={`chapter-content${currentAudio ? " chapter-content-with-audio" : ""}`}>
        <h3>{currentChapter.title}</h3>
        {currentAudio && (
          <div className="chapter-progress">
            <div
              className="chapter-progress-fill"
              style={{
                width: `${Math.min(
                  100,
                  Math.max(
                    0,
                    ((audioRef.current?.currentTime ?? audioTime) / Math.max(audioRef.current?.duration || 1, 1)) * 100,
                  ),
                )}%`,
              }}
            />
          </div>
        )}
        <div ref={contentRef} className="chapter-text epub-html" onClick={handleContentClick} />
      </main>

      {currentAudio && (
        <Player
          src={chapterAudioUrl(book.book_id, currentAudio.chapter_id)}
          chapterId={currentAudio.chapter_id}
          currentTime={audioTime}
          audioRef={audioRef}
          activeSentence={activeSentence}
          onTimeUpdate={setAudioTime}
          onPrevSentence={() => {
            if (!activeSentence || !audioRef.current) return;
            const idx = currentAudio.sentences.findIndex((sentenceItem) => sentenceItem.id === activeSentence.id);
            const target = currentAudio.sentences[Math.max(0, idx - 1)];
            if (target) audioRef.current.currentTime = target.t_start;
          }}
          onNextSentence={() => {
            if (!activeSentence || !audioRef.current) return;
            const idx = currentAudio.sentences.findIndex((sentenceItem) => sentenceItem.id === activeSentence.id);
            const target = currentAudio.sentences[
              Math.min(currentAudio.sentences.length - 1, idx + 1)
            ];
            if (target) audioRef.current.currentTime = target.t_start;
          }}
          onPrevChapter={() => setChapterIdx((prev) => Math.max(0, prev - 1))}
          onNextChapter={() => setChapterIdx((prev) => Math.min(maxChapterIdx, prev + 1))}
          onEnded={() => {
            if (!autoplayNext) return;
            setChapterIdx((prev) => {
              if (prev >= maxChapterIdx) return prev;
              const nextIdx = prev + 1;
              const nextChapter = book.chapters[nextIdx];
              if (!nextChapter) return prev;
              if (audioByChapterId.has(nextChapter.id)) shouldAutoplayRef.current = true;
              return nextIdx;
            });
          }}
          onSleepFired={() => {
            shouldAutoplayRef.current = false;
          }}
        />
      )}
    </div>
  );
}
