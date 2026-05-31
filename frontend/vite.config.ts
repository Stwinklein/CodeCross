import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // V1.3 keeps the React app and FastAPI app as two dev servers. The proxy
    // lets frontend code call `/api/...` without hard-coding a backend origin.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
