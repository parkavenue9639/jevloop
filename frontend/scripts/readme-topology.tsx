import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DecisionFlow } from "../src/components/DecisionFlow";
import { I18nProvider } from "../src/i18n";
import { applyRunEvent, emptyStream } from "../src/stream";
import { loopView } from "../src/loopView";
import type { RunEvent } from "../src/types";

/** Export the real component, not a separately maintained drawing. The caller
 * supplies only the checked-in, redacted event fixture; no backend is accessed. */
export function renderTopology(events: RunEvent[], lang: "en" | "zh") {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: () => lang } });
  try {
    const state = events.reduce(applyRunEvent, emptyStream()).lanes.jev;
    const view = loopView(state, { live: true, connected: true, done: false, error: false });
    const markup = renderToStaticMarkup(<I18nProvider><DecisionFlow
      sceneKey="readme-example" initialViewportWidth={1800} view={view} frame={state?.decisionFrame} step={view.step}
    /></I18nProvider>);
    return markup.slice(markup.indexOf('<svg viewBox="0 0'), markup.lastIndexOf("</svg>") + 6);
  } finally {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
}
