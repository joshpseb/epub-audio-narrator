/** Active sentence highlight: separate colors for light UI vs dark theme (readable contrast). */

export const READER_HIGHLIGHT_STORAGE_KEY = "epub-audio-narrator.reader-highlight.v1";

export type HighlightPresetId = "amber" | "mint" | "sky" | "rose" | "lavender" | "cream";

export type HighlightPair = { bg: string; fg: string };

export const HIGHLIGHT_PRESETS: Record<
  HighlightPresetId,
  { label: string; light: HighlightPair; dark: HighlightPair }
> = {
  amber: {
    label: "Amber",
    light: { bg: "#fef08a", fg: "#0f172a" },
    dark: { bg: "#ca8a04", fg: "#0f172a" },
  },
  mint: {
    label: "Mint",
    light: { bg: "#bbf7d0", fg: "#14532d" },
    dark: { bg: "#15803d", fg: "#ecfdf5" },
  },
  sky: {
    label: "Sky",
    light: { bg: "#bae6fd", fg: "#0c4a6e" },
    dark: { bg: "#0284c7", fg: "#f0f9ff" },
  },
  rose: {
    label: "Rose",
    light: { bg: "#fecdd3", fg: "#881337" },
    dark: { bg: "#e11d48", fg: "#fff1f2" },
  },
  lavender: {
    label: "Lavender",
    light: { bg: "#e9d5ff", fg: "#3b0764" },
    dark: { bg: "#7c3aed", fg: "#f5f3ff" },
  },
  cream: {
    label: "Soft gray",
    light: { bg: "#e2e8f0", fg: "#0f172a" },
    dark: { bg: "#475569", fg: "#f8fafc" },
  },
};

export const HIGHLIGHT_PRESET_ORDER: HighlightPresetId[] = [
  "amber",
  "mint",
  "sky",
  "rose",
  "lavender",
  "cream",
];

export function loadHighlightPreset(): HighlightPresetId {
  try {
    const raw = localStorage.getItem(READER_HIGHLIGHT_STORAGE_KEY);
    if (raw && raw in HIGHLIGHT_PRESETS) return raw as HighlightPresetId;
  } catch {
    // ignore
  }
  return "amber";
}
