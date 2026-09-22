import { useLang } from "../i18n";

/** Ivory/dark toggle. The ivory parchment is the default; the inverted slate
 *  rhythm is opt-in. Stored in localStorage, applied pre-paint by index.html. */
export function ThemeToggle() {
  const { lang } = useLang();
  const dark = document.documentElement.dataset.theme === "dark";
  const toggle = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("jevloop.theme", next);
  };
  return (
    <button
      onClick={toggle}
      className="rounded-full border border-line px-2 py-1 text-xs font-semibold text-ink2 transition-colors hover:text-ink"
      title={lang === "zh" ? (dark ? "切到象牙纸" : "切到石板墨") : dark ? "Ivory" : "Slate"}
    >
      {dark ? "☀" : "◐"}
    </button>
  );
}
