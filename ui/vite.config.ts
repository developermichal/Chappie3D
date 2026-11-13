import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "/",
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 3001,
    proxy: {
      "/api": "http://localhost:8004",
      "/ws/terminal": { target: "ws://localhost:8003", ws: true },
    },
  },
});
