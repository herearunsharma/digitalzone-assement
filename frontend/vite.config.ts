import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy /api to the backend so the browser only ever talks to one origin in
    // development. This is belt-and-braces alongside the CORS config on the
    // API: with the proxy, a reviewer who forgets to match ports still gets a
    // working app.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
