import { useState } from "react";
import { useLang } from "../i18n";
import { Icon } from "./Icon";

/** Explicit theme preference, applied pre-paint by index.html. */
export function ThemeToggle() {
  const { lang } = useLang();
  const [dark, setDark] = useState(
    () => document.documentElement.dataset.theme === "dark",
  );
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try { localStorage.setItem("jevloop.theme", next ? "dark" : "light"); } catch { /* in-memory preference */ }
  };
  return (
    <button
      onClick={toggle}
      className="icon-button"
      aria-label={lang === "zh" ? (dark ? "切换浅色" : "切换深色") : dark ? "Switch to light theme" : "Switch to dark theme"}
      title={lang === "zh" ? (dark ? "切换浅色" : "切换深色") : dark ? "Light theme" : "Dark theme"}
    >
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}
