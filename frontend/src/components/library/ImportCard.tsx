import type { DragEvent } from "react";

type Props = {
  activeFile: File | null;
  busy: boolean;
  onDrop: (event: DragEvent<HTMLLabelElement>) => void;
  onPickFile: (file: File | null) => void;
  onUpload: () => void;
};

export default function ImportCard({ activeFile, busy, onDrop, onPickFile, onUpload }: Props) {
  return (
    <section className="card">
      <p className="card-title">Import Book</p>
      <label
        className={`upload-zone${activeFile ? " has-file" : ""}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
      >
        <span className="upload-zone-icon">📄</span>
        {activeFile ? (
          <span className="upload-zone-filename">{activeFile.name}</span>
        ) : (
          <span className="upload-zone-label">
            <strong>Click to browse</strong> or drag/drop an EPUB file here
          </span>
        )}
        <input
          type="file"
          accept=".epub"
          style={{ display: "none" }}
          onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
        />
      </label>
      <button className="btn btn-primary btn-full" disabled={!activeFile || busy} onClick={onUpload}>
        {busy ? "Uploading…" : "Upload & Parse"}
      </button>
    </section>
  );
}
