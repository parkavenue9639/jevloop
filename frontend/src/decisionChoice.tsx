import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fetchConfig } from "./api";
import type { DecisionProvider } from "./types";

const STORAGE_KEY = "jevloop.decisionProvider";

function storedProvider(): DecisionProvider | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "laya" || stored === "jev" ? stored : null;
  } catch {
    return null;
  }
}

function persist(next: DecisionProvider) {
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* preference is best-effort */ }
}

const ChoiceContext = createContext<{
  provider: DecisionProvider;
  setProvider: (next: DecisionProvider) => void;
} | null>(null);

/** Shared with the composer and the flow diagram so a click updates both. */
export function DecisionChoiceProvider({ children }: { children: ReactNode }) {
  const [provider, setProviderState] = useState<DecisionProvider>(() => storedProvider() ?? "jev");
  useEffect(() => {
    if (storedProvider()) return;
    let cancel = false;
    fetchConfig()
      .then((config) => {
        if (cancel) return;
        if (config.decision_provider === "laya" || config.decision_provider === "jev") {
          setProviderState(config.decision_provider);
        }
      })
      .catch(() => undefined);
    return () => { cancel = true; };
  }, []);
  const setProvider = (next: DecisionProvider) => {
    setProviderState(next);
    persist(next);
  };
  return <ChoiceContext.Provider value={{ provider, setProvider }}>{children}</ChoiceContext.Provider>;
}

export function useDecisionChoice() {
  const shared = useContext(ChoiceContext);
  const [local, setLocal] = useState<DecisionProvider>("jev");
  return shared ?? { provider: local, setProvider: setLocal };
}
