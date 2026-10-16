/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LAZOS_TOKEN?: string;
  readonly VITE_LAZOS_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
