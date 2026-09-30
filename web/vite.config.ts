import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is bundled into the Python package, so users never need Node (D-011).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/objection/web_dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:6967" } },
});
