type LibraryBook = { book_id: string; title: string; authors: string[] };

type Props = {
  books: LibraryBook[];
  activeBookId: string | null;
  busy: boolean;
  onOpen: (bookId: string) => void;
  onRead: (bookId: string) => void;
  onDelete: (bookId: string) => void;
};

export default function LibraryListCard({ books, activeBookId, busy, onOpen, onRead, onDelete }: Props) {
  return (
    <section className="card">
      <p className="card-title">Library</p>
      {books.length === 0 ? (
        <p className="empty-library">No books yet — upload an EPUB above to get started.</p>
      ) : (
        <div className="book-list">
          {books.map((book) => (
            <div key={book.book_id} className={`book-item${activeBookId === book.book_id ? " book-item-active" : ""}`}>
              <span className="book-item-icon">📚</span>
              <div className="book-item-info">
                <span className="book-item-title">{book.title}</span>
                <span className="book-item-meta">
                  {book.authors.length > 0 ? book.authors.join(", ") : "Unknown author"}
                </span>
              </div>
              <div className="book-item-actions">
                <button
                  className="btn btn-sm btn-secondary"
                  disabled={busy || activeBookId === book.book_id}
                  onClick={() => onOpen(book.book_id)}
                >
                  {activeBookId === book.book_id ? "Opened" : "Open"}
                </button>
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => onRead(book.book_id)}>
                  Read
                </button>
                <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => onDelete(book.book_id)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
