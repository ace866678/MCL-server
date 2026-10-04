/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  turbopack: {
    // The repository root carries a second lockfile (pnpm-lock.yaml) for the
    // shell/bridge side of the project, so Turbopack would infer the workspace
    // root one level up and resolve modules from a directory with no
    // node_modules in it. This app is self-contained under web/.
    root: import.meta.dirname,
  },
};

export default nextConfig;