import type { SentenceTiming } from "./types";

const REMOVE_TAGS = new Set([
  "script",
  "style",
  "iframe",
  "img",
  "picture",
  "svg",
  "video",
  "audio",
  "object",
  "embed",
  "canvas",
  "map",
  "noscript",
  "track",
  "input",
  "button",
]);

/**
 * Remove media / script content and unwrap links while keeping typography (paragraphs, emphasis, headings).
 */
export function sanitizeReaderHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<div class="reader-sanitize">${html}</div>`, "text/html");
  const root = doc.querySelector(".reader-sanitize");
  if (!root) {
    return "";
  }

  REMOVE_TAGS.forEach((tag) => {
    root.querySelectorAll(tag).forEach((el) => el.remove());
  });

  root.querySelectorAll("*").forEach((el) => {
    [...el.attributes].forEach((at) => {
      const ln = at.name.toLowerCase();
      if (ln.startsWith("on")) {
        el.removeAttribute(at.name);
      }
    });
    if (el instanceof HTMLAnchorElement) {
      const href = el.getAttribute("href")?.trim().toLowerCase() ?? "";
      if (href.startsWith("javascript:")) {
        el.removeAttribute("href");
      }
    }
  });

  root.querySelectorAll("link").forEach((el) => el.remove());

  root.querySelectorAll("a").forEach((anchor) => {
    anchor.replaceWith(...Array.from(anchor.childNodes));
  });

  REMOVE_TAGS.forEach((tag) => {
    root.querySelectorAll(tag).forEach((el) => el.remove());
  });

  return root.innerHTML;
}

function isWs(ch: string): boolean {
  return ch === "\u00a0" || /\s/.test(ch);
}

function stripSlice(orig: string): { start: number; end: number } {
  let start = 0;
  let end = orig.length;
  while (start < end && isWs(orig[start])) start++;
  while (end > start && isWs(orig[end - 1])) end--;
  return { start, end };
}

type OrigRef = { node: Text; origIdx: number };
type RawRef = OrigRef | "sep";

/** Matches backend `text_cleaner._normalize_text` (sentence needles). */
function normalizePlainText(s: string): string {
  return s.replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * Same normalization as `_normalize_text` on the raw BS-style stream, while retaining
 * DOM offsets for each output character.
 */
function normalizePlainTextWithMap(raw: string, refs: RawRef[]): { flat: string; map: Array<OrigRef | null> } {
  const map: Array<OrigRef | null> = [];
  let flat = "";
  let i = 0;
  const n = raw.length;
  while (i < n && isWs(raw[i])) i++;
  while (i < n) {
    if (!isWs(raw[i])) {
      flat += raw[i];
      const rr = refs[i];
      map.push(rr === "sep" ? null : rr);
      i++;
      continue;
    }
    const runStart = i;
    while (i < n && isWs(raw[i])) i++;
    if (i >= n) break;
    if (flat.length === 0) continue;

    const prevRef = refs[runStart - 1];
    const nextRef = refs[i];
    const midRef = refs[runStart];
    let bridge: OrigRef | null = null;
    if (
      prevRef !== "sep" &&
      nextRef !== "sep" &&
      midRef !== "sep" &&
      prevRef.node === nextRef.node
    ) {
      bridge = { node: prevRef.node, origIdx: midRef.origIdx };
    }

    flat += " ";
    map.push(bridge);
  }
  return { flat, map };
}

/**
 * BeautifulSoup-style `get_text(" ", strip=True)` over live DOM: stripped slices joined by spaces.
 */
function collectRawStream(root: HTMLElement): { raw: string; refs: RawRef[] } {
  let raw = "";
  const refs: RawRef[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let first = true;
  let n = walker.nextNode();
  while (n) {
    const textNode = n as Text;
    const orig = textNode.textContent ?? "";
    const { start, end } = stripSlice(orig);
    if (start < end) {
      if (!first) {
        raw += " ";
        refs.push("sep");
      }
      first = false;
      for (let i = start; i < end; i++) {
        raw += orig[i];
        refs.push({ node: textNode, origIdx: i });
      }
    }
    n = walker.nextNode();
  }
  return { raw, refs };
}

function collectNormalizedStream(root: HTMLElement): { flat: string; map: Array<OrigRef | null> } {
  const { raw, refs } = collectRawStream(root);
  return normalizePlainTextWithMap(raw, refs);
}

function splitWrapSpan(textNode: Text, localStart: number, localEnd: number, sid: string): void {
  const full = textNode.textContent ?? "";
  if (localStart < 0 || localEnd > full.length || localStart >= localEnd) return;
  const before = full.slice(0, localStart);
  const mid = full.slice(localStart, localEnd);
  const after = full.slice(localEnd);
  const parent = textNode.parentNode;
  if (!parent || !mid) return;
  const span = document.createElement("span");
  span.className = "sentence";
  span.dataset.sid = sid;
  span.textContent = mid;

  const frag = document.createDocumentFragment();
  if (before) frag.appendChild(document.createTextNode(before));
  frag.appendChild(span);
  if (after) frag.appendChild(document.createTextNode(after));
  parent.replaceChild(frag, textNode);
}

function wrapMatchFromMap(map: Array<OrigRef | null>, flatLo: number, flatHi: number, sid: string): void {
  type Run = { node: Text; o0: number; o1: number };
  const runs: Run[] = [];
  let k = flatLo;
  while (k < flatHi) {
    while (k < flatHi && map[k] === null) k++;
    if (k >= flatHi) break;
    const first = map[k]!;
    const node = first.node;
    let o0 = first.origIdx;
    let o1 = first.origIdx + 1;
    k++;
    while (k < flatHi) {
      const r = map[k];
      if (r !== null && r.node === node) {
        o0 = Math.min(o0, r.origIdx);
        o1 = Math.max(o1, r.origIdx + 1);
        k++;
      } else break;
    }
    runs.push({ node, o0, o1 });
  }

  for (let idx = runs.length - 1; idx >= 0; idx--) {
    const run = runs[idx];
    splitWrapSpan(run.node, run.o0, run.o1, sid);
  }
}

/**
 * Infer sentence spans in existing HTML by sequentially matching synthesized sentence text against DOM text stream.
 */
export function wrapSentenceSpans(root: HTMLElement, sentences: SentenceTiming[]): boolean {
  if (!sentences.length) return false;
  let wrapped = false;
  let cursor = 0;

  const ordered = [...sentences].sort((a, b) => a.t_start - b.t_start || a.text_start - b.text_start);

  for (const s of ordered) {
    const needleRaw = normalizePlainText(s.text ?? "");
    if (!needleRaw.length) continue;

    const { flat, map } = collectNormalizedStream(root);
    if (flat.length === 0 || map.length !== flat.length) continue;

    let i = flat.indexOf(needleRaw, cursor);
    if (i < 0) continue;

    const end = i + needleRaw.length;
    wrapped = true;
    cursor = end;

    wrapMatchFromMap(map, i, end, s.id);
  }

  return wrapped;
}
