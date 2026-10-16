import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource/chakra-petch/400.css";
import "@fontsource/chakra-petch/500.css";
import "@fontsource/chakra-petch/600.css";
import "./styles/globals.css";

import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { useApp } from "./stores/app";

if (import.meta.env.DEV) {
  // Dev-only handle for visual testing of AI states.
  (window as unknown as { __lazos: typeof useApp }).__lazos = useApp;
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
