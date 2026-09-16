import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listVoices, previewVoice } from "../lib/api";
import type { VoiceInfo } from "../lib/types";
import SimpleAudioPlayer from "./SimpleAudioPlayer";

type Props = {
  language: string;
  currentVoiceName: string;
  provider?: string;
  speakingRate?: number;
  pitch?: number;
  onChange: (voiceName: string) => void;
};

const languageCodeMap: Record<string, string> = {
  en: "en-US",
  zh: "cmn-CN",
  ko: "ko-KR",
  ja: "ja-JP",
  fr: "fr-FR",
  de: "de-DE",
  es: "es-ES",
};

const tierOrder: Array<VoiceInfo["tier"]> = ["Studio", "Neural2", "Wavenet", "Standard", "Chirp", "Other"];

export default function VoicePicker({
  language,
  currentVoiceName,
  provider = "kokoro",
  speakingRate = 1,
  pitch = 0,
  onChange,
}: Props) {
  const [voices, setVoices] = useState<VoiceInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewCacheRef = useRef<Record<string, string>>({});
  const audioRef = useRef<HTMLAudioElement>(null);

  const languageCode = languageCodeMap[language] ?? language;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listVoices(languageCode, provider)
      .then((rows) => {
        if (!cancelled) {
          setVoices(rows);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load voices";
          setError(msg);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [languageCode, provider]);

  useEffect(() => {
    if (voices.length > 0 && !voices.some((v) => v.name === currentVoiceName)) {
      onChange(voices[0].name);
    }
  }, [voices, currentVoiceName, onChange]);

  const grouped = useMemo(() => {
    const byTier = new Map<VoiceInfo["tier"], VoiceInfo[]>();
    for (const tier of tierOrder) {
      byTier.set(tier, []);
    }
    for (const voice of voices) {
      const list = byTier.get(voice.tier) ?? [];
      list.push(voice);
      byTier.set(voice.tier, list);
    }
    return byTier;
  }, [voices]);

  const handlePreview = useCallback(async (voiceName: string) => {
    if (!voiceName) return;
    setPreviewing(true);
    setError(null);
    try {
      const cacheKey = `${provider}:${languageCode}:${voiceName}:${speakingRate}:${pitch}`;
      let url = previewCacheRef.current[cacheKey];
      if (!url) {
        const blob = await previewVoice(voiceName, languageCode, undefined, speakingRate, pitch, provider);
        url = URL.createObjectURL(blob);
        previewCacheRef.current[cacheKey] = url;
      }
      if (audioRef.current) {
        audioRef.current.src = url;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to preview voice";
      setError(msg);
    } finally {
      setPreviewing(false);
    }
  }, [languageCode, speakingRate, pitch, provider]);

  useEffect(() => {
    if (!currentVoiceName || voices.length === 0 || loading) return;
    void handlePreview(currentVoiceName);
  }, [currentVoiceName, voices.length, loading, handlePreview]);

  return (
    <div className="voice-picker">
      <label>Voice ({languageCode})</label>
      <select value={currentVoiceName} onChange={(e) => onChange(e.target.value)} disabled={loading || voices.length === 0}>
        {tierOrder.map((tier) => {
          const tierVoices = grouped.get(tier) ?? [];
          if (tierVoices.length === 0) return null;
          return (
            <optgroup key={tier} label={tier}>
              {tierVoices.map((voice) => (
                <option key={voice.name} value={voice.name}>
                  {voice.name} [{voice.gender}]
                </option>
              ))}
            </optgroup>
          );
        })}
      </select>
      {error && <p className="voice-picker-error">{error}</p>}
      <SimpleAudioPlayer audioRef={audioRef} />
    </div>
  );
}
