import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@claread/design-tokens"],
  ...(process.env.CLAREAD_E2E_TEST === "1"
    ? { distDir: ".next-e2e-test" }
    : {}),
  images: {
    remotePatterns: [
      {
        protocol: "http",
        hostname: "127.0.0.1",
        port: "8000",
      },
    ],
  },
};

export default nextConfig;
