import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // A stray package-lock.json at the user's home directory makes Next.js
  // misdetect the workspace root; pin it to this project explicitly.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
