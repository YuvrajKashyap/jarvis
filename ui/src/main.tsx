import { registerSW } from "virtual:pwa-register";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";

registerSW({ immediate: false });

const root = document.getElementById("root");
if (root === null) {
  throw new Error("JARVIS root element is missing");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
