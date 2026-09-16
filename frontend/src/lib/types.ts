export type Chapter = {
  id: string;
  title: string;
  html: string;
  plain_text: string;
  word_count: number;
};

export type BookManifest = {
  book_id: string;
  title: string;
  authors: string[];
  chapters: Chapter[];
};

export type SentenceTiming = {
  id: string;
  text: string;
  lang: string;
  text_start: number;
  text_end: number;
  t_start: number;
  t_end: number;
  chapter_id: string;
};

export type ChapterAudioManifest = {
  chapter_id: string;
  chapter_title: string;
  audio_file: string;
  sentences: SentenceTiming[];
};

export type SynthesisManifest = {
  book_id: string;
  title: string;
  chapters: ChapterAudioManifest[];
};

export type VoiceConfig = {
  language: string;
  voice_name: string;
  speaking_rate: number;
  pitch: number;
};

export type VoiceInfo = {
  name: string;
  gender: string;
  language_codes: string[];
  natural_sample_rate_hertz: number;
  tier: "Studio" | "Neural2" | "Wavenet" | "Standard" | "Chirp" | "Other";
};

export type ChapterSelection = {
  chapter_ids: string[];
};

export type SynthesizeRequest = {
  cleanup: {
    min_chapter_words: number;
    drop_frontmatter: boolean;
    drop_backmatter: boolean;
    drop_toc: boolean;
  };
  voices: VoiceConfig[];
  selection: ChapterSelection;
  incremental: boolean;
  tts_provider: "kokoro" | "google";
};

export type Volume = {
  index: number;
  name: string;
  chapter_ids: string[];
  start_index: number;
  end_index: number;
};

export type ChapterSummary = {
  id: string;
  title: string;
  word_count: number;
};

export type BookStructure = {
  book_id: string;
  title: string;
  authors: string[];
  chapter_summaries: ChapterSummary[];
  volumes: Volume[];
  synthesized_chapter_ids: string[];
};

export type JobState = {
  status: "queued" | "running" | "done" | "error";
  progress: number;
  message: string;
  error?: string;
};
