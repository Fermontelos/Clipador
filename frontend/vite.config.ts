/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Onde o "clipador web" está rodando (a porta é a de [revisao] porta no config.toml).
const API = process.env.CLIPADOR_API ?? "http://127.0.0.1:8765";
const PORTA_DO_VITE = 5173;
const ORIGENS_DO_VITE = new Set([
  `http://localhost:${PORTA_DO_VITE}`,
  `http://127.0.0.1:${PORTA_DO_VITE}`,
]);

// "npm run build:estatica": a demo que roda só no navegador, para o GitHub Pages, que serve o
// site na subpasta do repositório (https://<usuário>.github.io/Clipador/).
const PASTA_NO_PAGES = process.env.CLIPADOR_BASE ?? "/Clipador/";

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  base: mode === "estatica" ? PASTA_NO_PAGES : "/",
  build: { outDir: mode === "estatica" ? "dist-estatica" : "dist" },
  server: {
    host: "127.0.0.1", // só este PC, como a API
    port: PORTA_DO_VITE,
    strictPort: true,
    proxy: {
      // No desenvolvimento ("npm run dev"), o Vite repassa /api para o FastAPI. A API recusa
      // pedidos que mudam algo vindos de outra origem (ADR 0007), e o navegador manda a do
      // Vite: o proxy troca SÓ ela pela da API. Uma origem estranha continua sendo recusada.
      "/api": {
        target: API,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (pedido) => {
            const origem = pedido.getHeader("origin");
            if (typeof origem === "string" && ORIGENS_DO_VITE.has(origem)) {
              pedido.setHeader("origin", API);
            }
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/testes/preparar.ts"],
    // npm run test:cobertura: quanto do código da interface os testes executam.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/testes/**", "src/main.tsx"],
      reporter: ["text-summary", "text", "json-summary"],
      reportsDirectory: "../dados/cobertura-frontend",
      thresholds: { lines: 80 }, // abaixo disso, o comando (e a CI) falha
    },
  },
}));
