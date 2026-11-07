import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const BACKEND = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": BACKEND,
      "/ws": { target: BACKEND.replace("http", "ws"), ws: true },
    },
  },
});
