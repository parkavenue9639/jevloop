import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DecisionChoiceProvider } from "./decisionChoice";
import { I18nProvider } from "./i18n";
import "./styles.css";
import "./console.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <DecisionChoiceProvider>
        <App />
      </DecisionChoiceProvider>
    </I18nProvider>
  </StrictMode>,
);
