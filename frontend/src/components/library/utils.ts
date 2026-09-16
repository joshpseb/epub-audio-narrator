export type ScopeMode = "all" | "volume" | "range" | "next_n";

export function fmtSec(s: number): string {
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export function perMillionRateForVoiceName(
  voiceName: string | undefined,
  provider: "kokoro" | "google" = "kokoro",
): number {
  if (provider !== "google") {
    return 0;
  }
  if (!voiceName) {
    return 4;
  }
  if (/-Studio-/i.test(voiceName)) {
    return 160;
  }
  if (/-Neural2-/i.test(voiceName)) {
    return 16;
  }
  if (/-Wavenet-/i.test(voiceName)) {
    return 4;
  }
  return 4;
}
