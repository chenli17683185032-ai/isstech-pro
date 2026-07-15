import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("../src/isstech_replay/web_dist", import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
  },
});
