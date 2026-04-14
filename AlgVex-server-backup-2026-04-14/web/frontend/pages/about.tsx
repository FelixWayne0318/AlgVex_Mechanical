"use client";

import { useRouter } from "next/router";
import Head from "next/head";
import { Shield, Zap, Activity } from "lucide-react";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Card, CardContent } from "@/components/ui/card";
import { useTranslation, type Locale } from "@/lib/i18n";

const features = [
  {
    icon: Activity,
    titleKey: "about.strategy",
    descKey: "about.strategyDesc",
  },
  {
    icon: Shield,
    titleKey: "about.risk",
    descKey: "about.riskDesc",
  },
  {
    icon: Zap,
    titleKey: "about.tech",
    descKey: "about.techDesc",
  },
];

const techStack = [
  {
    name: "NautilusTrader",
    description: "高性能算法交易平台 (Cython 指标计算)",
    version: "1.224.0",
  },
  {
    name: "Prism 评分引擎",
    description: "3 维预判评分 (Structure / Divergence / Order Flow) + net_raw 阈值决策",
    version: "",
  },
  {
    name: "SRP 均值回归",
    description: "VWMA + RSI-MFI 通道 + DCA 分层入场，468 天回测 93% 胜率",
    version: "v5.0",
  },
  {
    name: "141 Typed Features",
    description: "从 13 类数据源提取结构化特征，驱动 3 维 anticipatory scoring",
    version: "",
  },
  {
    name: "DCA 仓位管理",
    description: "固定 SL/TP + 几何加仓 + 虚拟 DCA 追踪，R/R = 0.8:1",
    version: "v48.0+",
  },
  {
    name: "Binance Futures",
    description: "BTCUSDT 永续合约，原生 Trailing Stop + OCO 订单",
    version: "Futures API",
  },
  {
    name: "Python",
    description: "核心策略和数据处理语言",
    version: "3.12+",
  },
  {
    name: "Next.js + FastAPI",
    description: "Web 管理前后端",
    version: "14 / 0.115",
  },
];

export default function AboutPage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);

  return (
    <>
      <Head>
        <title>关于 - AlgVex</title>
        <meta
          name="description"
          content="关于 AlgVex 双策略算法交易系统"
        />
      </Head>

      <div className="min-h-screen gradient-bg">
        <Header locale={locale} t={t} />

        {/* pt-24 accounts for floating rounded header with extra spacing */}
        <main className="pt-24 pb-16 px-4">
          <div className="container mx-auto max-w-4xl">
            {/* Page Header */}
            <div className="text-center mb-16">
              <h1 className="text-4xl font-bold mb-4">{t("about.title")}</h1>
              <p className="text-xl text-muted-foreground max-w-2xl mx-auto">
                双策略架构的算法交易系统，基于数据驱动的预判评分和均值回归策略，在加密货币市场实现稳定交易。
              </p>
            </div>

            {/* Core Features */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
              {features.map((feature) => {
                const Icon = feature.icon;
                return (
                  <Card
                    key={feature.titleKey}
                    className="border-border/50 text-center"
                  >
                    <CardContent className="p-8">
                      <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-primary/10 flex items-center justify-center">
                        <Icon className="h-8 w-8 text-primary" />
                      </div>
                      <h3 className="text-xl font-semibold mb-3">
                        {t(feature.titleKey)}
                      </h3>
                      <p className="text-muted-foreground">
                        {t(feature.descKey)}
                      </p>
                    </CardContent>
                  </Card>
                );
              })}
            </div>

            {/* How It Works */}
            <Card className="border-border/50 mb-16">
              <CardContent className="p-8">
                <h2 className="text-2xl font-bold mb-6 text-center">
                  工作原理
                </h2>
                <div className="space-y-6">
                  <div className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-semibold">
                      1
                    </div>
                    <div>
                      <h4 className="font-semibold mb-1">13 类数据源聚合</h4>
                      <p className="text-muted-foreground">
                        每 20 分钟聚合：技术指标 (RSI, MACD, ATR, ADX, OBV)
                        跨 3 个时间框架 (1D/4H/30M)、订单流 (CVD, taker ratios)、
                        衍生品 (OI, liquidations, funding rate)、订单簿深度、
                        市场情绪 — 共 141 个 typed features 自动提取。
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-semibold">
                      2
                    </div>
                    <div>
                      <h4 className="font-semibold mb-1">Prism 3 维预判评分</h4>
                      <p className="text-muted-foreground">
                        从 13 类数据源提取 141 个 typed features，计算 Structure（均值回归 + S/R）、
                        Divergence（RSI/MACD/OBV 背离）、Order Flow（CVD/OI/清算）三维评分，
                        net_raw 阈值决策，零 API 调用，&lt;1 秒延迟。
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-semibold">
                      3
                    </div>
                    <div>
                      <h4 className="font-semibold mb-1">DCA + 固定 SL/TP</h4>
                      <p className="text-muted-foreground">
                        TP=4% / SL=5% (R/R=0.8:1)，几何 1.5x DCA 分层入场（最多 4 层），
                        虚拟 DCA 持续追踪均价。Trailing Stop 保护利润。
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-semibold">
                      4
                    </div>
                    <div>
                      <h4 className="font-semibold mb-1">多层安全保障</h4>
                      <p className="text-muted-foreground">
                        每层独立 SL/TP + 币安原生 Trailing Stop。
                        Emergency SL 自动重试兜底。清算缓冲监控。
                        FR exhaustion 打破方向死循环。
                        交易记忆系统自动评估每笔交易并生成 lesson。
                      </p>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Tech Stack */}
            <Card className="border-border/50">
              <CardContent className="p-8">
                <h2 className="text-2xl font-bold mb-6 text-center">
                  技术栈
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {techStack.map((tech) => (
                    <div
                      key={tech.name}
                      className="p-4 rounded-lg bg-muted/30 border border-border/50"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="font-semibold">{tech.name}</h4>
                        <span className="text-xs text-primary bg-primary/10 px-2 py-1 rounded">
                          {tech.version}
                        </span>
                      </div>
                      <p className="text-sm text-muted-foreground">
                        {tech.description}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        </main>

        <Footer t={t} />
      </div>
    </>
  );
}
