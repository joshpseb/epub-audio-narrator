import type { BookManifest, BookStructure, JobState, SynthesisManifest, SynthesizeRequest, VoiceInfo } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

function withAuthHeaders(headers?: HeadersInit): HeadersInit {
  const base: Record<string, string> = {};
  if (API_TOKEN) {
    base["x-api-key"] = API_TOKEN;
  }
  return { ...base, ...(headers ?? {}) };
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

export async function listBooks(): Promise<Array<{ book_id: string; title: string; authors: string[] }>> {
  return parseJson(await fetch(`${API_BASE}/books`, { headers: withAuthHeaders() }));
}

export async function uploadBook(file: File): Promise<BookManifest> {
  const formData = new FormData();
  formData.append("epub_file", file);
  return parseJson(
    await fetch(`${API_BASE}/books`, {
      method: "POST",
      headers: withAuthHeaders(),
      body: formData,
    }),
  );
}

export async function fetchBookManifest(bookId: string): Promise<BookManifest> {
  return parseJson(await fetch(`${API_BASE}/books/${bookId}`, { headers: withAuthHeaders() }));
}

export async function refreshBookTitles(bookId: string): Promise<{
  book_id: string;
  status: string;
  manifest_updated: boolean;
  synthesis_chapters_updated: number;
}> {
  return parseJson(
    await fetch(`${API_BASE}/books/${bookId}/refresh-titles`, {
      method: "POST",
      headers: withAuthHeaders(),
    }),
  );
}

export async function deleteBook(bookId: string): Promise<void> {
  await parseJson(
    await fetch(`${API_BASE}/books/${bookId}`, {
      method: "DELETE",
      headers: withAuthHeaders(),
    }),
  );
}

export async function startSynthesis(bookId: string, payload: SynthesizeRequest): Promise<void> {
  await parseJson(
    await fetch(`${API_BASE}/books/${bookId}/synthesize`, {
      method: "POST",
      headers: withAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    }),
  );
}

export function watchSynthesisProgress(bookId: string, onData: (job: JobState) => void): () => void {
  const source = new EventSource(
    `${API_BASE}/books/${bookId}/progress${API_TOKEN ? `?api_key=${encodeURIComponent(API_TOKEN)}` : ""}`,
  );
  source.onmessage = (event) => {
    onData(JSON.parse(event.data));
  };
  source.onerror = () => {
    source.close();
  };
  return () => source.close();
}

export async function fetchAudioManifest(bookId: string): Promise<SynthesisManifest> {
  return parseJson(await fetch(`${API_BASE}/books/${bookId}/manifest`, { headers: withAuthHeaders() }));
}

/** Returns null when no synthesized audio exists yet (HTTP 404 from manifest route). */
export async function fetchAudioManifestOptional(bookId: string): Promise<SynthesisManifest | null> {
  const response = await fetch(`${API_BASE}/books/${bookId}/manifest`, { headers: withAuthHeaders() });
  if (response.status === 404) {
    return null;
  }
  return parseJson(response);
}

export async function fetchBookStructure(bookId: string): Promise<BookStructure> {
  return parseJson(await fetch(`${API_BASE}/books/${bookId}/structure`, { headers: withAuthHeaders() }));
}

export async function listVoices(languageCode: string, provider = "kokoro"): Promise<VoiceInfo[]> {
  const params = new URLSearchParams({ language_code: languageCode, provider });
  return parseJson(await fetch(`${API_BASE}/voices?${params.toString()}`, { headers: withAuthHeaders() }));
}

export async function previewVoice(
  voiceName: string,
  languageCode: string,
  text?: string,
  speakingRate = 1,
  pitch = 0,
  provider = "kokoro",
): Promise<Blob> {
  const response = await fetch(`${API_BASE}/voices/preview`, {
    method: "POST",
    headers: withAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      voice_name: voiceName,
      language_code: languageCode,
      text,
      speaking_rate: speakingRate,
      pitch,
      provider,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.blob();
}

export function chapterAudioUrl(bookId: string, chapterId: string): string {
  return `${API_BASE}/books/${bookId}/chapter/${chapterId}`;
}

export function exportM4bUrl(
  bookId: string,
  opts?: { volumeIndex?: number; startIndex?: number; endIndex?: number },
): string {
  const params = new URLSearchParams();
  if (opts?.volumeIndex !== undefined) params.set("volume_index", String(opts.volumeIndex));
  if (opts?.startIndex !== undefined) params.set("start_index", String(opts.startIndex));
  if (opts?.endIndex !== undefined) params.set("end_index", String(opts.endIndex));
  const qs = params.toString();
  return `${API_BASE}/books/${bookId}/export.m4b${qs ? `?${qs}` : ""}`;
}

/** Fallback download name when Content-Disposition is absent. */
export function suggestedM4bFilename(bookTitle: string): string {
  const safe = bookTitle.replace(/[/\\?*:"|<>\u0000-\u001f]/g, "_").trim().replace(/^\.+|\.+$/g, "") || "audiobook";
  return /\.m4b$/i.test(safe) ? safe : `${safe}.m4b`;
}

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = header.match(/filename\*=(?:UTF-8'')?([^;\n]+)/i);
  if (star) {
    try {
      const raw = star[1].trim().replace(/^["']|["']$/g, "");
      const decoded = decodeURIComponent(raw);
      if (decoded) return decoded;
    } catch {
      // malformed filename*
    }
  }
  const quoted = header.match(/filename="([^"]+)"/i);
  if (quoted?.[1]) return quoted[1];
  const plain = header.match(/filename=([^;\n]+)/i);
  if (plain?.[1]) return plain[1].replace(/^["']|["']$/g, "").trim();
  return fallback;
}

/**
 * Stream response to a Blob, reporting download progress when Content-Length is present.
 */
export async function downloadM4bExport(
  exportUrl: string,
  fallbackFilename: string,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<void> {
  const response = await fetch(exportUrl, { headers: withAuthHeaders() });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText || "Export failed");
  }
  const totalHeader = response.headers.get("Content-Length");
  const total = totalHeader ? Number(totalHeader) : null;
  const body = response.body;
  if (!body) {
    const blob = await response.blob();
    onProgress?.(blob.size, blob.size);
    _saveBlobDownload(blob, response.headers.get("Content-Disposition"), fallbackFilename);
    return;
  }

  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      chunks.push(value);
      loaded += value.length;
      onProgress?.(loaded, Number.isFinite(total as number) ? (total as number) : null);
    }
  }
  const blob = new Blob(chunks as BlobPart[], { type: "audio/mp4" });
  _saveBlobDownload(blob, response.headers.get("Content-Disposition"), fallbackFilename);
}

function _saveBlobDownload(blob: Blob, contentDisposition: string | null, fallbackFilename: string): void {
  const name = filenameFromContentDisposition(contentDisposition, fallbackFilename);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = name;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
