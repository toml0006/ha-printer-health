import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8099",
      "/health": "http://localhost:8099",
      "/config": "http://localhost:8099",
      "/printers": "http://localhost:8099",
      "/print": "http://localhost:8099",
      "/discovery": "http://localhost:8099",
      "/templates": "http://localhost:8099",
      "/actions": "http://localhost:8099",
      "/ping": "http://localhost:8099",
      "/cards": "http://localhost:8099",
      "/guidance": "http://localhost:8099",
    },
  },
});
