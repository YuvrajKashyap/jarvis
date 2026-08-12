import { createRoot } from "react-dom/client";

import { Showcase, showcaseScenario } from "./showcase";

const root = document.getElementById("root");
if (root === null) throw new Error("JARVIS showcase root is missing");

createRoot(root).render(<Showcase scenario={showcaseScenario(window.location.search)} />);
