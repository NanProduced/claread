import { resolve } from "node:path";

const config = {
  viteConfig: {
    resolve: {
      alias: {
        "@": resolve(import.meta.dirname, "./src"),
      },
    },
  },
};

export default config;
