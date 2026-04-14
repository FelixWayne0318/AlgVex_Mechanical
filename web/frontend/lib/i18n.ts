export type Locale = "en" | "zh";

export const defaultLocale: Locale = "zh";

export const translations: Record<Locale, Record<string, string>> = {
  en: {
    // Navigation
    "nav.home": "Home",
    "nav.dashboard": "Dashboard",
    "nav.performance": "Performance",
    "nav.copy": "Copy Trading",
    "nav.srp": "SRP Strategy",
    "nav.mechanical": "Prism",
    "nav.about": "About",

    // Hero section
    "hero.title": "Algorithmic",
    "hero.title2": "Crypto Trading",
    "hero.subtitle": "Dual-strategy automated trading: Prism scoring + SRP mean reversion",
    "hero.cta": "Start Copy Trading",
    "hero.stats": "View Performance",

    // Stats
    "stats.totalReturn": "Total Return",
    "stats.winRate": "Win Rate",
    "stats.maxDrawdown": "Max Drawdown",
    "stats.totalTrades": "Total Trades",
    "stats.activeStatus": "Trading Status",
    "stats.running": "Running",
    "stats.stopped": "Stopped",

    // Performance
    "perf.title": "Performance Analytics",
    "perf.subtitle": "Real-time trading performance from Binance Futures",
    "perf.pnlCurve": "Cumulative P&L",
    "perf.period": "Period",
    "perf.days30": "30 Days",
    "perf.days90": "90 Days",
    "perf.days180": "180 Days",
    "perf.days365": "1 Year",

    // Copy Trading
    "copy.title": "Copy Trading",
    "copy.subtitle": "Follow my trades on these exchanges",
    "copy.howTo": "How to Copy Trade",
    "copy.step1": "Click the exchange link below",
    "copy.step2": "Log in to your exchange account",
    "copy.step3": "Follow the copy trading instructions",
    "copy.disclaimer": "Trading involves risk. Past performance is not indicative of future results.",

    // About
    "about.title": "About AlgVex",
    "about.strategy": "Trading Strategy",
    "about.strategyDesc": "Dual-strategy system: Prism 3-dimension scoring + SRP VWMA mean reversion",
    "about.risk": "Risk Management",
    "about.riskDesc": "Automated stop-loss, take-profit, trailing stop, and DCA layers to protect your capital",
    "about.tech": "Technology",
    "about.techDesc": "Built on NautilusTrader framework with 141-feature anticipatory scoring engine",

    // Footer
    "footer.disclaimer": "Disclaimer: Trading cryptocurrencies involves significant risk. Past performance does not guarantee future results. Trade responsibly.",
    "footer.rights": "All rights reserved",

    // Common
    "common.loading": "Loading...",
    "common.error": "Error loading data",
    "common.lastUpdated": "Last updated",
  },
  zh: {
    // Navigation
    "nav.home": "首页",
    "nav.dashboard": "监控面板",
    "nav.performance": "业绩",
    "nav.copy": "跟单",
    "nav.srp": "SRP 策略",
    "nav.mechanical": "Prism",
    "nav.about": "关于",

    // Hero section
    "hero.title": "算法驱动",
    "hero.title2": "加密货币交易",
    "hero.subtitle": "双策略自动交易：Prism 预判评分 + SRP 均值回归",
    "hero.cta": "开始跟单",
    "hero.stats": "查看业绩",

    // Stats
    "stats.totalReturn": "总收益率",
    "stats.winRate": "胜率",
    "stats.maxDrawdown": "最大回撤",
    "stats.totalTrades": "总交易次数",
    "stats.activeStatus": "交易状态",
    "stats.running": "运行中",
    "stats.stopped": "已停止",

    // Performance
    "perf.title": "业绩分析",
    "perf.subtitle": "来自币安 Futures 的实时交易数据",
    "perf.pnlCurve": "累计盈亏",
    "perf.period": "周期",
    "perf.days30": "30 天",
    "perf.days90": "90 天",
    "perf.days180": "180 天",
    "perf.days365": "1 年",

    // Copy Trading
    "copy.title": "跟单交易",
    "copy.subtitle": "在以下交易所跟随我的交易",
    "copy.howTo": "如何跟单",
    "copy.step1": "点击下方交易所链接",
    "copy.step2": "登录您的交易所账户",
    "copy.step3": "按照跟单说明操作",
    "copy.disclaimer": "交易有风险，过往业绩不代表未来表现。",

    // About
    "about.title": "关于 AlgVex",
    "about.strategy": "交易策略",
    "about.strategyDesc": "双策略系统：Prism 3 维预判评分 + SRP VWMA 均值回归",
    "about.risk": "风险管理",
    "about.riskDesc": "自动止损、止盈、移动止损 + DCA 分层入场，保护您的资金",
    "about.tech": "技术架构",
    "about.techDesc": "基于 NautilusTrader 框架，141 features 预判评分引擎",

    // Footer
    "footer.disclaimer": "免责声明：加密货币交易涉及重大风险。过往业绩不保证未来收益。请谨慎交易。",
    "footer.rights": "版权所有",

    // Common
    "common.loading": "加载中...",
    "common.error": "数据加载错误",
    "common.lastUpdated": "最后更新",
  },
};

export function useTranslation(locale: Locale) {
  const t = (key: string): string => {
    return translations[locale][key] || key;
  };
  return { t, locale };
}
