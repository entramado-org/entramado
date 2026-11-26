/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Sitio 100 % estático en out/ (spec 077): los datos son los YAML de ../datos, leídos al construir.
  output: "export",
  // next/image is not used anywhere; keep the optimizer off so /_next/image is never a code path
  // (GHSA-2xp9-vwfh-vxw4).
  images: { unoptimized: true },
};
export default nextConfig;
