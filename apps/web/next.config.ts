import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@claread/design-tokens"],
  // Gate E2E servers use their own build directories. They must never share a
  // Next dev lock with a developer's regular or manually started gate server.
  ...(process.env.CLAREAD_ASK_ACTIVITY_R2_TEST
    ? { distDir: ".next-e2e-ask-activity-r2-test" }
    : process.env.CLAREAD_E2E_SPIKE_TEST
      ? { distDir: ".next-e2e-spike-test" }
      : process.env.CLAREAD_E2E_GATE_TEST
        ? { distDir: ".next-e2e-gate-test" }
        : process.env.CLAREAD_GATE_TEST
          ? { distDir: ".next-gate-test" }
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
