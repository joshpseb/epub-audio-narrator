import { useEffect } from "react";

type Props = {
  bookTitle: string;
  busy: boolean;
  onDismiss: () => void;
  onConfirm: () => void;
};

export default function DeleteBookModal({ bookTitle, busy, onDismiss, onConfirm }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onDismiss]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onDismiss();
      }}
    >
      <div className="modal-dialog card" role="dialog" aria-modal="true" aria-labelledby="delete-book-title">
        <h2 id="delete-book-title" className="modal-dialog-title">
          Delete this book?
        </h2>
        <div className="modal-dialog-body">
          <p>
            <strong className="modal-dialog-strong">{bookTitle}</strong>
          </p>
          <p className="modal-dialog-muted">
            This permanently removes the EPUB, synthesized audio, and downloads for this book. This cannot be undone.
          </p>
        </div>
        <div className="modal-dialog-actions">
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onDismiss}>
            Cancel
          </button>
          <button type="button" className="btn btn-danger" disabled={busy} onClick={onConfirm}>
            {busy ? "Deleting…" : "Delete permanently"}
          </button>
        </div>
      </div>
    </div>
  );
}
