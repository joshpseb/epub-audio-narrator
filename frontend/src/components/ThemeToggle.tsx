import { useTheme } from "../theme";

/** Compact control for Library header (on brand bar) vs Reader sidebar (muted). */
export default function ThemeToggle({ variant }: { variant: "header" | "inline" }) {
  const { theme, toggleTheme } = useTheme();
  const dark = theme === "dark";

  return (
    <button
      type="button"
      className={`theme-toggle btn btn-sm ${variant === "header" ? "theme-toggle-header" : "theme-toggle-inline"}`}
      onClick={toggleTheme}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
    >
      <span aria-hidden className="theme-toggle-icon">{dark ? "☀️" : "🌙"}</span>
    </button>
  );
}
