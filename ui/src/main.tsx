import { registerSW } from "virtual:pwa-register";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";
import { enablePhonePwaUpdates } from "./pwa-updates";

if ("__TAURI_INTERNALS__" in window) {
  void navigator.serviceWorker
    ?.getRegistrations()
    .then((registrations) =>
      Promise.all(registrations.map((registration) => registration.unregister())),
    );
} else {
  enablePhonePwaUpdates(registerSW);
}

const root = document.getElementById("root");
if (root === null) {
  throw new Error("JARVIS root element is missing");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
