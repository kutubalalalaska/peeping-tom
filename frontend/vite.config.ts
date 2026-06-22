import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` serves the UI and proxies /api to the FastAPI server.
// Prod: `npm run build` emits ./dist, which the server serves via WEB_DIR.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  build: { outDir: "dist" },
});
