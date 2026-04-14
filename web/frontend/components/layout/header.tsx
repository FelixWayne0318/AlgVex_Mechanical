"use client";

import Link from "next/link";
import { useRouter } from "next/router";
import { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import {
  Menu,
  X,
  Globe,
  Bot,
  Users,
  Percent,
  BarChart3,
  Activity,
  Zap,
  ChevronDown,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Locale } from "@/lib/i18n";

interface HeaderProps {
  locale: Locale;
  t: (key: string) => string;
}

export function Header({ locale, t }: HeaderProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [metricsExpanded, setMetricsExpanded] = useState(false);
  const metricsRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (metricsRef.current && !metricsRef.current.contains(event.target as Node)) {
        setMetricsExpanded(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const { data: status } = useSWR(mounted ? "/api/public/system-status" : null, { refreshInterval: 30000 });
  const { data: sentiment } = useSWR(mounted ? "/api/trading/long-short-ratio/BTCUSDT" : null, { refreshInterval: 60000 });
  const { data: markPrice } = useSWR(mounted ? "/api/trading/mark-price/BTCUSDT" : null, { refreshInterval: 30000 });
  const { data: openInterest } = useSWR(mounted ? "/api/trading/open-interest/BTCUSDT" : null, { refreshInterval: 60000 });
  const { data: ticker } = useSWR(mounted ? "/api/trading/ticker/BTCUSDT" : null, { refreshInterval: 10000 });
  const { data: mechState } = useSWR(mounted ? "/api/public/mechanical/state" : null, { refreshInterval: 30000 });
  const { data: branding } = useSWR(mounted ? "/api/public/site-branding" : null, { refreshInterval: 300000 });

  const toggleLocale = () => {
    const newLocale = locale === "en" ? "zh" : "en";
    router.push(router.pathname, router.asPath, { locale: newLocale });
  };

  const navItems = [
    { href: "/", label: t("nav.home") },
    { href: "/dashboard", label: t("nav.dashboard") },
    { href: "/mechanical", label: "Prism" },
    { href: "/performance", label: t("nav.performance") },
    { href: "/srp", label: t("nav.srp") },
    { href: "/copy", label: t("nav.copy") },
  ];

  const rawRatio = sentiment?.data?.[0]?.long_short_ratio || sentiment?.longShortRatio;
  const hasLongShortData = rawRatio !== undefined && rawRatio !== null && rawRatio > 0;
  const longShortRatio = hasLongShortData ? rawRatio : 1;
  const longPercent = hasLongShortData ? (longShortRatio / (longShortRatio + 1)) * 100 : 50;
  const fundingRate = markPrice?.funding_rate ? markPrice.funding_rate * 100 : markPrice?.lastFundingRate ? parseFloat(markPrice.lastFundingRate) * 100 : 0;
  const oiValue = openInterest?.value || 0;
  const formatOI = (value: number) => { if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`; if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`; return "--"; };
  const volume24h = ticker?.quote_volume_24h || 0;
  const formatVolume = (value: number) => { if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`; if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`; return "--"; };
  const signal = mechState?.signal || "HOLD";
  const netRaw = mechState?.net_raw ?? 0;
  const getSignalColor = (s: string) => {
    if (s === "LONG") return "text-green-500";
    if (s === "SHORT") return "text-red-500";
    return "text-foreground";
  };
  const getSignalDisplay = (s: string) => {
    if (mechState?.status === "no_data") return "Waiting";
    return s;
  };

  return (
    <header className="fixed top-4 inset-x-0 z-50 px-4 overflow-hidden">
      {/* DipSway Style: Transparent header, each group has its own background */}
      <div className="max-w-7xl mx-auto flex h-14 items-center justify-between min-w-0">

        {/* Group 1: Logo - No background */}
        <Link href="/" className="flex items-center gap-2.5 group shrink-0">
          {branding?.logo_url ? (
            <img src={branding.logo_url} alt={branding?.site_name || "AlgVex"} className="h-8 w-8 rounded-xl object-contain" />
          ) : (
            <div className="h-8 w-8 rounded-xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-md shadow-primary/20">
              <span className="text-primary-foreground font-bold text-sm">A</span>
            </div>
          )}
          <span className="text-lg font-bold">{branding?.site_name || "AlgVex"}</span>
        </Link>

        {/* Group 2: Navigation - Own rounded background (Desktop + Landscape) */}
        <nav className="hidden lg:flex landscape:flex items-center gap-1 bg-background/60 backdrop-blur-xl border border-border/30 rounded-xl p-1 ml-8 landscape:ml-3 shrink-0 landscape:shrink">
          {navItems.map((item) => {
            const isActive = router.pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`px-4 landscape:px-2.5 py-1.5 rounded-lg text-sm landscape:text-xs font-medium transition-all whitespace-nowrap ${
                  isActive
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-background/50"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Spacer to push metrics group closer together */}
        <div className="hidden lg:block landscape:block flex-1 landscape:min-w-0" />

        {/* Metrics Group Container - smaller gaps between Bot/Signal/Markets */}
        <div className="hidden lg:flex landscape:flex items-center gap-1.5 landscape:gap-1 shrink-0">
          {/* Group 3: Bot Status */}
          {mounted && (
            <div className="flex items-center gap-1.5 landscape:gap-1 px-3 landscape:px-2 py-2 landscape:py-1.5 bg-background/60 backdrop-blur-xl border border-border/30 rounded-xl">
              <Bot className={`h-3.5 w-3.5 ${status?.trading_active ? "text-green-500" : "text-muted-foreground"}`} />
              <span className="text-xs landscape:text-[10px] text-muted-foreground landscape:hidden">Bot:</span>
              <span className={`text-xs landscape:text-[10px] font-medium ${status?.trading_active ? "text-green-500" : "text-muted-foreground"}`}>
                {status?.trading_active ? "Running" : "Offline"}
              </span>
            </div>
          )}

          {/* Group 4: Mechanical Signal */}
          {mounted && (
            <div className={`flex items-center gap-1.5 landscape:gap-1 px-3 landscape:px-2 py-2 landscape:py-1.5 backdrop-blur-xl border border-border/30 rounded-xl ${
              signal === "BUY" || signal === "LONG" ? "bg-green-500/10" :
              signal === "SELL" || signal === "SHORT" ? "bg-red-500/10" :
              signal === "NO_DATA" ? "bg-yellow-500/10" : "bg-background/60"
            }`}>
              <Zap className={`h-3.5 w-3.5 ${getSignalColor(signal)}`} />
              <span className="text-xs landscape:text-[10px] text-muted-foreground landscape:hidden">Signal:</span>
              <span className={`text-xs landscape:text-[10px] font-semibold ${getSignalColor(signal)}`}>{getSignalDisplay(signal)}</span>
            </div>
          )}

          {/* Group 5: Markets Dropdown */}
          {mounted && (
            <div ref={metricsRef} className="relative">
              <button
                onClick={() => setMetricsExpanded(!metricsExpanded)}
                className="flex items-center gap-1.5 landscape:gap-1 px-3 landscape:px-2 py-2 landscape:py-1.5 bg-background/60 backdrop-blur-xl border border-border/30 rounded-xl text-xs landscape:text-[10px] font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                <TrendingUp className="h-3.5 w-3.5" />
                <span>Markets</span>
                <ChevronDown className={`h-3 w-3 transition-transform ${metricsExpanded ? "rotate-180" : ""}`} />
              </button>

              {metricsExpanded && (
                <div className="absolute top-full right-0 mt-2 w-64 bg-background/95 backdrop-blur-xl border border-border/40 rounded-xl shadow-xl p-3 space-y-2 z-50">
                  <div className="text-xs text-muted-foreground font-medium mb-2">Market Metrics</div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
                    <div className="flex items-center gap-2">
                      <Users className={`h-4 w-4 ${hasLongShortData ? (longPercent > 50 ? "text-green-500" : "text-red-500") : "text-muted-foreground"}`} />
                      <span className="text-sm">Long/Short</span>
                    </div>
                    <span className={`text-sm font-semibold ${hasLongShortData ? (longPercent > 50 ? "text-green-500" : "text-red-500") : "text-muted-foreground"}`}>{hasLongShortData ? `${longPercent.toFixed(1)}%` : '--'}</span>
                  </div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
                    <div className="flex items-center gap-2">
                      <Percent className={`h-4 w-4 ${fundingRate >= 0 ? "text-green-500" : "text-red-500"}`} />
                      <span className="text-sm">Funding Rate</span>
                    </div>
                    <span className={`text-sm font-semibold ${fundingRate >= 0 ? "text-green-500" : "text-red-500"}`}>{fundingRate >= 0 ? "+" : ""}{fundingRate.toFixed(4)}%</span>
                  </div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="h-4 w-4 text-blue-500" />
                      <span className="text-sm">Open Interest</span>
                    </div>
                    <span className="text-sm font-semibold">{formatOI(oiValue)}</span>
                  </div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
                    <div className="flex items-center gap-2">
                      <Activity className="h-4 w-4 text-purple-500" />
                      <span className="text-sm">24h Volume</span>
                    </div>
                    <span className="text-sm font-semibold">{formatVolume(volume24h)}</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Group 6: Language + CTA (Desktop + Landscape) */}
        <div className="hidden lg:flex landscape:flex items-center gap-2 landscape:gap-1 ml-3 landscape:ml-1.5 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={toggleLocale}
            className="h-9 landscape:h-8 px-3 landscape:px-2 bg-background/60 backdrop-blur-xl border border-border/30 rounded-xl hover:bg-background/80"
          >
            <Globe className="h-4 w-4 landscape:h-3.5 landscape:w-3.5" />
            <span className="ml-1.5 landscape:ml-1 text-xs landscape:text-[10px] font-medium">{locale.toUpperCase()}</span>
          </Button>
          {/* CTA hidden in landscape - Copy Trading already in nav */}
          <Link href="/copy" className="landscape:hidden">
            <Button size="sm" className="h-9 rounded-xl px-4 bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70 shadow-md shadow-primary/20">
              {t("hero.cta")}
            </Button>
          </Link>
        </div>

        {/* Mobile Portrait: Show Bot + Signal + Menu button */}
        <div className="flex lg:hidden landscape:hidden items-center gap-2">
          {/* Bot Status - Mobile Portrait */}
          {mounted && (
            <div className="flex items-center gap-1 px-2 py-1.5 bg-background/60 backdrop-blur-xl border border-border/30 rounded-lg">
              <Bot className={`h-3 w-3 ${status?.trading_active ? "text-green-500" : "text-muted-foreground"}`} />
              <span className={`text-[10px] font-medium ${status?.trading_active ? "text-green-500" : "text-muted-foreground"}`}>
                {status?.trading_active ? "Running" : "Offline"}
              </span>
            </div>
          )}

          {/* Signal - Mobile Portrait */}
          {mounted && (
            <div className={`flex items-center gap-1 px-2 py-1.5 backdrop-blur-xl border border-border/30 rounded-lg ${
              signal === "BUY" || signal === "LONG" ? "bg-green-500/10" :
              signal === "SELL" || signal === "SHORT" ? "bg-red-500/10" :
              signal === "NO_DATA" ? "bg-yellow-500/10" : "bg-background/60"
            }`}>
              <Zap className={`h-3 w-3 ${getSignalColor(signal)}`} />
              <span className={`text-[10px] font-semibold ${getSignalColor(signal)}`}>{getSignalDisplay(signal)}</span>
            </div>
          )}

          {/* Mobile menu button */}
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 bg-background/60 backdrop-blur-xl border border-border/30 rounded-xl"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          >
            {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>
      </div>

      {/* Mobile Navigation Menu (Portrait only) */}
      {mobileMenuOpen && (
        <div className="lg:hidden landscape:hidden mt-3 max-w-7xl mx-auto bg-background/95 backdrop-blur-xl border border-border/40 rounded-2xl p-4">
          <nav className="flex flex-col gap-1 mb-4">
            {navItems.map((item) => {
              const isActive = router.pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-4 py-2.5 rounded-xl transition-all ${
                    isActive ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  }`}
                  onClick={() => setMobileMenuOpen(false)}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          {/* Market Data - 4 metrics */}
          {mounted && (
            <div className="border-t border-border/30 pt-4 mb-4">
              <p className="text-xs text-muted-foreground px-2 mb-3 font-medium">Market Data</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center gap-2 p-3 rounded-xl bg-muted/30">
                  <Users className={`h-4 w-4 ${hasLongShortData ? (longPercent > 50 ? "text-green-500" : "text-red-500") : "text-muted-foreground"}`} />
                  <span className="text-sm">{hasLongShortData ? `${longPercent.toFixed(0)}% Long` : '--'}</span>
                </div>
                <div className="flex items-center gap-2 p-3 rounded-xl bg-muted/30">
                  <Percent className={`h-4 w-4 ${fundingRate >= 0 ? "text-green-500" : "text-red-500"}`} />
                  <span className={`text-sm ${fundingRate >= 0 ? "text-green-500" : "text-red-500"}`}>
                    {fundingRate >= 0 ? "+" : ""}{fundingRate.toFixed(4)}%
                  </span>
                </div>
                <div className="flex items-center gap-2 p-3 rounded-xl bg-muted/30">
                  <BarChart3 className="h-4 w-4 text-blue-500" />
                  <span className="text-sm">OI {formatOI(oiValue)}</span>
                </div>
                <div className="flex items-center gap-2 p-3 rounded-xl bg-muted/30">
                  <Activity className="h-4 w-4 text-purple-500" />
                  <span className="text-sm">{formatVolume(volume24h)}</span>
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center justify-between pt-4 border-t border-border/30">
            <Button variant="ghost" size="sm" onClick={toggleLocale} className="rounded-xl">
              <Globe className="h-4 w-4 mr-2" />
              {locale === "en" ? "中文" : "English"}
            </Button>
            <Link href="/copy" onClick={() => setMobileMenuOpen(false)}>
              <Button size="sm" className="rounded-xl bg-gradient-to-r from-primary to-primary/80">
                {t("hero.cta")}
              </Button>
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
