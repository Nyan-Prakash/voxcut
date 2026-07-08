import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the backend's static dir so FastAPI serves one artifact.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/voxcut/static",
    emptyOutDir: true,
  },
  server: {
    // Dev proxy: the SPA on :5173 talks to the backend on :8484.
    proxy: {
      "/api": "http://127.0.0.1:8484",
    },
  },
});
