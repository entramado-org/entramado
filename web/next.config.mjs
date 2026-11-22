/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a minimal standalone server (.next/standalone) for the production image.
  output: "standalone",
  // next/image is not used anywhere; keep the optimizer off so /_next/image is
  // never a code path (GHSA-2xp9-vwfh-vxw4). Caddy also answers 404 for it.
  images: { unoptimized: true },
  // With the optimizer off, sharp (an optional dependency of Next since 15) and its
  // native libvips builds are dead weight in the standalone image: keep them out.
  outputFileTracingExcludes: { "*": ["**/node_modules/sharp/**/*", "**/node_modules/@img/**/*"] },
};
export default nextConfig;
