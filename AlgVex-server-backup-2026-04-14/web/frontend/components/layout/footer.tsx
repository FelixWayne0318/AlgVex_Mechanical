"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import useSWR from "swr";
import { MessageCircle, Github, Users } from "lucide-react";

interface FooterProps {
  t: (key: string) => string;
}

export function Footer({ t }: FooterProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Fetch site branding
  const { data: branding } = useSWR(
    mounted ? "/api/public/site-branding" : null,
    { refreshInterval: 300000 }
  );

  // Fetch social links from API
  const { data: socialLinks } = useSWR(
    mounted ? "/api/public/social-links" : null,
    { refreshInterval: 300000 }
  );

  const twitterUrl = socialLinks?.find?.((l: any) => l.platform === "twitter")?.url;
  const telegramUrl = socialLinks?.find?.((l: any) => l.platform === "telegram")?.url;
  const telegramChatUrl = socialLinks?.find?.((l: any) => l.platform === "telegram_chat")?.url;
  const githubUrl = socialLinks?.find?.((l: any) => l.platform === "github")?.url || "https://github.com/FelixWayne0318/AlgVex";

  return (
    <footer className="border-t border-border bg-background/50">
      <div className="container mx-auto px-4 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
          {/* Brand */}
          <div className="col-span-1 md:col-span-2">
            <Link href="/" className="flex items-center space-x-2 mb-4">
              {branding?.logo_url ? (
                <img
                  src={branding.logo_url}
                  alt={branding?.site_name || "AlgVex"}
                  className="h-8 w-8 rounded-lg object-contain"
                />
              ) : (
                <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
                  <span className="text-primary-foreground font-bold text-lg">A</span>
                </div>
              )}
              <span className="text-xl font-bold">{branding?.site_name || "AlgVex"}</span>
            </Link>
            <p className="text-sm text-muted-foreground max-w-md">
              基于 NautilusTrader 框架的算法交易系统，双策略架构：Prism 预判评分 + SRP 均值回归。
            </p>
          </div>

          {/* Links */}
          <div>
            <h4 className="font-semibold mb-4">Links</h4>
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li>
                <Link href="/performance" className="hover:text-foreground transition-colors">
                  Performance
                </Link>
              </li>
              <li>
                <Link href="/copy" className="hover:text-foreground transition-colors">
                  Copy Trading
                </Link>
              </li>
              <li>
                <Link href="/about" className="hover:text-foreground transition-colors">
                  About
                </Link>
              </li>
            </ul>
          </div>

          {/* Social */}
          <div>
            <h4 className="font-semibold mb-4">Connect</h4>
            <div className="flex flex-col space-y-3">
              {/* Telegram Groups */}
              <div className="flex space-x-4">
                {telegramUrl && (
                  <a
                    href={telegramUrl}
                    className="flex items-center space-x-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <MessageCircle className="h-4 w-4" />
                    <span>Signals</span>
                  </a>
                )}
                {telegramChatUrl && (
                  <a
                    href={telegramChatUrl}
                    className="flex items-center space-x-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Users className="h-4 w-4" />
                    <span>Community</span>
                  </a>
                )}
              </div>
              {/* Other Social */}
              <div className="flex space-x-4">
                {twitterUrl && (
                  <a
                    href={twitterUrl}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                    </svg>
                  </a>
                )}
                <a
                  href={githubUrl}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Github className="h-5 w-5" />
                </a>
              </div>
            </div>
          </div>
        </div>

        {/* Disclaimer */}
        <div className="mt-8 pt-8 border-t border-border">
          <p className="text-xs text-muted-foreground text-center">
            {t("footer.disclaimer")}
          </p>
          <p className="text-xs text-muted-foreground text-center mt-2">
            &copy; {new Date().getFullYear()} {branding?.site_name || "AlgVex"}. {t("footer.rights")}.
          </p>
        </div>
      </div>
    </footer>
  );
}
