import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src")
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/email-outbox": "http://127.0.0.1:8000",
      "/invoices": "http://127.0.0.1:8000",
      "/export": "http://127.0.0.1:8000"
    }
  }
});
