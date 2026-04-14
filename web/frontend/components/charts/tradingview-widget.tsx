"use client";

import { useEffect, useRef, memo } from "react";

interface TradingViewWidgetProps {
  symbol?: string;
  interval?: string;
  theme?: "dark" | "light";
  height?: number | string;
  autosize?: boolean;
  showToolbar?: boolean;
  showDetails?: boolean;
  allowSymbolChange?: boolean;
}

function TradingViewWidgetComponent({
  symbol = "BINANCE:BTCUSDT.P",
  interval = "15",
  theme = "dark",
  height = 500,
  autosize = true,
  showToolbar = true,
  showDetails = true,
  allowSymbolChange = false,
}: TradingViewWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const scriptRef = useRef<HTMLScriptElement | null>(null);

  useEffect(() => {
    // Clean up previous widget
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    // Create container for the widget
    const widgetContainer = document.createElement("div");
    widgetContainer.className = "tradingview-widget-container__widget";
    widgetContainer.style.height = autosize ? "100%" : `${height}px`;
    widgetContainer.style.width = "100%";

    if (containerRef.current) {
      containerRef.current.appendChild(widgetContainer);
    }

    // Create and load the script
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: autosize,
      symbol: symbol,
      interval: interval,
      timezone: "Etc/UTC",
      theme: theme,
      style: "1",
      locale: "en",
      allow_symbol_change: allowSymbolChange,
      calendar: false,
      support_host: "https://www.tradingview.com",
      hide_top_toolbar: !showToolbar,
      hide_legend: !showDetails,
      hide_side_toolbar: true,  // Hide drawing tools - users view only
      withdateranges: true,
      save_image: false,
      details: showDetails,
      hotlist: false,
      studies: [],  // Clean chart - AI analysis shown in sidebar
      container_id: "tradingview_widget_container",
      backgroundColor: "rgba(10, 14, 23, 1)",
      gridColor: "rgba(30, 41, 59, 0.3)",
      toolbar_bg: "#0a0e17",
      enable_publishing: false,
      hide_volume: false,
    });

    widgetContainer.appendChild(script);
    scriptRef.current = script;

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [symbol, interval, theme, height, autosize, showToolbar, showDetails, allowSymbolChange]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container w-full rounded-xl overflow-hidden"
      style={{ height: autosize ? "100%" : height }}
    />
  );
}

export const TradingViewWidget = memo(TradingViewWidgetComponent);

// Mini chart widget for small displays
interface MiniChartWidgetProps {
  symbol?: string;
  height?: number;
  colorTheme?: "dark" | "light";
}

function MiniChartWidgetComponent({
  symbol = "BINANCE:BTCUSDT.P",
  height = 200,
  colorTheme = "dark",
}: MiniChartWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    const widgetContainer = document.createElement("div");
    widgetContainer.className = "tradingview-widget-container__widget";

    if (containerRef.current) {
      containerRef.current.appendChild(widgetContainer);
    }

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbol: symbol,
      width: "100%",
      height: height,
      locale: "en",
      dateRange: "1D",
      colorTheme: colorTheme,
      isTransparent: true,
      autosize: true,
      largeChartUrl: "",
      noTimeScale: false,
      chartOnly: false,
    });

    widgetContainer.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [symbol, height, colorTheme]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height }}
    />
  );
}

export const MiniChartWidget = memo(MiniChartWidgetComponent);

// Ticker tape widget
interface TickerTapeWidgetProps {
  symbols?: Array<{ proName: string; title: string }>;
  colorTheme?: "dark" | "light";
}

function TickerTapeWidgetComponent({
  symbols = [
    { proName: "BINANCE:BTCUSDT.P", title: "BTC/USDT" },
    { proName: "BINANCE:ETHUSDT.P", title: "ETH/USDT" },
    { proName: "BINANCE:SOLUSDT.P", title: "SOL/USDT" },
    { proName: "BINANCE:BNBUSDT.P", title: "BNB/USDT" },
    { proName: "CRYPTOCAP:TOTAL", title: "Total Crypto" },
    { proName: "CRYPTOCAP:BTC.D", title: "BTC Dominance" },
  ],
  colorTheme = "dark",
}: TickerTapeWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    const widgetContainer = document.createElement("div");
    widgetContainer.className = "tradingview-widget-container__widget";

    if (containerRef.current) {
      containerRef.current.appendChild(widgetContainer);
    }

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbols: symbols,
      showSymbolLogo: true,
      isTransparent: true,
      displayMode: "adaptive",
      colorTheme: colorTheme,
      locale: "en",
    });

    widgetContainer.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [symbols, colorTheme]);

  return (
    <div ref={containerRef} className="tradingview-widget-container" />
  );
}

export const TickerTapeWidget = memo(TickerTapeWidgetComponent);

// Technical analysis widget
interface TechnicalAnalysisWidgetProps {
  symbol?: string;
  interval?: string;
  colorTheme?: "dark" | "light";
  height?: number;
}

function TechnicalAnalysisWidgetComponent({
  symbol = "BINANCE:BTCUSDT.P",
  interval = "30m",
  colorTheme = "dark",
  height = 400,
}: TechnicalAnalysisWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    const widgetContainer = document.createElement("div");
    widgetContainer.className = "tradingview-widget-container__widget";

    if (containerRef.current) {
      containerRef.current.appendChild(widgetContainer);
    }

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      interval: interval,
      width: "100%",
      isTransparent: true,
      height: height,
      symbol: symbol,
      showIntervalTabs: true,
      displayMode: "single",
      locale: "en",
      colorTheme: colorTheme,
    });

    widgetContainer.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [symbol, interval, colorTheme, height]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height }}
    />
  );
}

export const TechnicalAnalysisWidget = memo(TechnicalAnalysisWidgetComponent);

// Market overview widget
interface MarketOverviewWidgetProps {
  colorTheme?: "dark" | "light";
  height?: number;
}

function MarketOverviewWidgetComponent({
  colorTheme = "dark",
  height = 400,
}: MarketOverviewWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    const widgetContainer = document.createElement("div");
    widgetContainer.className = "tradingview-widget-container__widget";

    if (containerRef.current) {
      containerRef.current.appendChild(widgetContainer);
    }

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-market-overview.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      colorTheme: colorTheme,
      dateRange: "1D",
      showChart: true,
      locale: "en",
      width: "100%",
      height: height,
      largeChartUrl: "",
      isTransparent: true,
      showSymbolLogo: true,
      showFloatingTooltip: true,
      plotLineColorGrowing: "rgba(34, 197, 94, 1)",
      plotLineColorFalling: "rgba(239, 68, 68, 1)",
      gridLineColor: "rgba(30, 41, 59, 0.3)",
      scaleFontColor: "rgba(134, 142, 150, 1)",
      belowLineFillColorGrowing: "rgba(34, 197, 94, 0.12)",
      belowLineFillColorFalling: "rgba(239, 68, 68, 0.12)",
      belowLineFillColorGrowingBottom: "rgba(34, 197, 94, 0)",
      belowLineFillColorFallingBottom: "rgba(239, 68, 68, 0)",
      symbolActiveColor: "rgba(34, 197, 94, 0.12)",
      tabs: [
        {
          title: "Crypto",
          symbols: [
            { s: "BINANCE:BTCUSDT.P", d: "BTC/USDT" },
            { s: "BINANCE:ETHUSDT.P", d: "ETH/USDT" },
            { s: "BINANCE:SOLUSDT.P", d: "SOL/USDT" },
            { s: "BINANCE:BNBUSDT.P", d: "BNB/USDT" },
            { s: "BINANCE:XRPUSDT.P", d: "XRP/USDT" },
            { s: "BINANCE:ADAUSDT.P", d: "ADA/USDT" },
          ],
          originalTitle: "Crypto",
        },
      ],
    });

    widgetContainer.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, [colorTheme, height]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height }}
    />
  );
}

export const MarketOverviewWidget = memo(MarketOverviewWidgetComponent);
