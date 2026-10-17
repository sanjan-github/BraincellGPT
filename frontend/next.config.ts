import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Explicitly set the tracing root to this directory so Next.js does not
  // mis-detect the workspace root when other lockfiles exist higher up the tree.
  outputFileTracingRoot: path.join(__dirname),
};
export default nextConfig;

