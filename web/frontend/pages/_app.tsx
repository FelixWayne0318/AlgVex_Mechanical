import { useEffect } from "react";
import type { AppProps } from "next/app";
import Head from "next/head";
import useSWR, { SWRConfig } from "swr";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import "@/styles/globals.css";

// NOTE: We use <link> tags in _document.tsx for Google Fonts instead of next/font/google.
// next/font/google downloads fonts at BUILD TIME — if Google Fonts is unreachable
// (firewall, China GFW, network issues), the ENTIRE build fails with:
//   Error [NextFontError]: Failed to fetch font `Inter`.
// <link> tags load fonts at RUNTIME in the browser, with graceful fallback.

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) {
    const error = new Error("API request failed") as Error & { status: number };
    error.status = res.status;
    throw error;
  }
  return res.json();
};

export default function App({ Component, pageProps }: AppProps) {
  // Fetch branding settings for dynamic favicon
  // Note: This hook runs outside SWRConfig, so it needs explicit options
  const { data: branding } = useSWR("/api/public/site-branding", fetcher, {
    refreshInterval: 300000, // 5 minutes
    revalidateOnFocus: false,
    keepPreviousData: true,
  });

  // Update favicon dynamically
  useEffect(() => {
    if (branding?.favicon_url) {
      const link: HTMLLinkElement =
        document.querySelector("link[rel*='icon']") ||
        document.createElement("link");
      link.type = "image/x-icon";
      link.rel = "shortcut icon";
      link.href = branding.favicon_url;
      document.head.appendChild(link);
    }
  }, [branding?.favicon_url]);

  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: false,
        keepPreviousData: true,
        dedupingInterval: 5000,
        shouldRetryOnError: false,
        onError: (error) => {
          // Silently handle API errors - SWR will set error state
          // This prevents unhandled rejections from crashing the app
          if (process.env.NODE_ENV === 'development') {
            console.warn('[SWR] API error:', error.message);
          }
        },
      }}
    >
      <Head>
        <title>{branding?.site_name || "AlgVex"} - 算法交易</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
      </Head>
      <main className="font-sans antialiased min-h-screen bg-background text-foreground">
        <ErrorBoundary>
          <Component {...pageProps} />
        </ErrorBoundary>
      </main>
    </SWRConfig>
  );
}
