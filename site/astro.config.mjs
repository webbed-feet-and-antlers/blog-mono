// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import astroExpressiveCode from 'astro-expressive-code';
import mdx from '@astrojs/mdx';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

// Project page on an org Pages site: https://webbed-feet-and-antlers.github.io/blog-mono/
export default defineConfig({
  site: 'https://webbed-feet-and-antlers.github.io',
  base: '/blog-mono',
  integrations: [
    // Expressive Code MUST come before mdx so it can process fenced code blocks.
    astroExpressiveCode({
      themes: ['github-light', 'github-dark'],
      styleOverrides: {
        borderRadius: '0.5rem',
        codeFontFamily:
          "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
      },
    }),
    mdx(),
    react(),
    sitemap(),
  ],
  markdown: {
    remarkPlugins: [remarkMath],
    rehypePlugins: [rehypeKatex],
  },
  vite: {
    plugins: [tailwindcss()],
  },
});
