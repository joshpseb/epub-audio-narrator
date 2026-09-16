import { useState } from "react";
import Library from "./components/Library";
import Reader from "./components/Reader";
import type { BookManifest } from "./lib/types";

export default function App() {
  const [readerBook, setReaderBook] = useState<BookManifest | null>(null);

  return (
    <>
      {/* Keep Library mounted while reading so synth/upload state survives Back from Reader. */}
      <div hidden={readerBook !== null}>
        <Library onOpenBook={setReaderBook} />
      </div>
      {readerBook && <Reader book={readerBook} onBack={() => setReaderBook(null)} />}
    </>
  );
}
