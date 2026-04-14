"use client";

import { useRouter } from "next/router";
import Head from "next/head";
import useSWR from "swr";
import { ExternalLink, Copy, CheckCircle } from "lucide-react";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTranslation, type Locale } from "@/lib/i18n";

// Exchange icons/colors
const exchangeConfig: Record<string, { color: string; bgColor: string }> = {
  binance: { color: "#F0B90B", bgColor: "rgba(240, 185, 11, 0.1)" },
  bybit: { color: "#F7A600", bgColor: "rgba(247, 166, 0, 0.1)" },
  okx: { color: "#FFFFFF", bgColor: "rgba(255, 255, 255, 0.1)" },
  bitget: { color: "#00F0FF", bgColor: "rgba(0, 240, 255, 0.1)" },
};

export default function CopyPage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);

  const { data: copyLinks } = useSWR("/api/public/copy-trading");
  const { data: socialLinks } = useSWR("/api/public/social-links");

  const telegram = socialLinks?.find((l: any) => l.platform === "telegram");
  const telegramChat = socialLinks?.find((l: any) => l.platform === "telegram_chat");
  const twitter = socialLinks?.find((l: any) => l.platform === "twitter");

  return (
    <>
      <Head>
        <title>跟单交易 - AlgVex</title>
        <meta
          name="description"
          content="在主要加密货币交易所跟随我的交易"
        />
      </Head>

      <div className="min-h-screen gradient-bg">
        <Header locale={locale} t={t} />

        {/* pt-24 accounts for floating rounded header with extra spacing */}
        <main className="pt-24 pb-16 px-4">
          <div className="container mx-auto max-w-4xl">
            {/* Page Header */}
            <div className="text-center mb-12">
              <h1 className="text-4xl font-bold mb-4">{t("copy.title")}</h1>
              <p className="text-xl text-muted-foreground">
                {t("copy.subtitle")}
              </p>
            </div>

            {/* Copy Trading Links */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-12">
              {copyLinks?.map((link: any) => {
                const config = exchangeConfig[link.exchange] || {
                  color: "#00d4aa",
                  bgColor: "rgba(0, 212, 170, 0.1)",
                };

                return (
                  <Card
                    key={link.exchange}
                    className="border-border/50 hover:border-primary/50 transition-all"
                  >
                    <CardContent className="p-6">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-4">
                          <div
                            className="w-12 h-12 rounded-xl flex items-center justify-center text-xl font-bold"
                            style={{
                              backgroundColor: config.bgColor,
                              color: config.color,
                            }}
                          >
                            {link.exchange.charAt(0).toUpperCase()}
                          </div>
                          <div>
                            <h3 className="font-semibold text-lg">{link.name}</h3>
                            <p className="text-sm text-muted-foreground">
                              {link.exchange.charAt(0).toUpperCase() +
                                link.exchange.slice(1)}
                            </p>
                          </div>
                        </div>
                        {link.url ? (
                          <a
                            href={link.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <Button>
                              Copy
                              <ExternalLink className="ml-2 h-4 w-4" />
                            </Button>
                          </a>
                        ) : (
                          <Button disabled variant="outline">
                            即将上线
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>

            {/* How To Section */}
            <Card className="border-border/50 mb-12">
              <CardHeader>
                <CardTitle>{t("copy.howTo")}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {[1, 2, 3].map((step) => (
                    <div key={step} className="flex items-start gap-4">
                      <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-semibold">
                        {step}
                      </div>
                      <p className="text-muted-foreground pt-1">
                        {t(`copy.step${step}`)}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Social Links */}
            <Card className="border-border/50 mb-12">
              <CardHeader>
                <CardTitle>加入社区</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-4">
                  {telegram?.url && (
                    <a
                      href={telegram.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <Button variant="outline" size="lg">
                        <svg
                          className="w-5 h-5 mr-2"
                          viewBox="0 0 24 24"
                          fill="currentColor"
                        >
                          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.2-.08-.06-.19-.04-.27-.02-.12.02-1.96 1.25-5.54 3.66-.52.36-1 .53-1.42.52-.47-.01-1.37-.26-2.03-.48-.82-.27-1.47-.42-1.42-.88.03-.24.37-.49 1.02-.75 3.98-1.73 6.64-2.87 7.97-3.43 3.8-1.57 4.59-1.85 5.1-1.85.11 0 .37.03.53.17.14.12.18.28.2.45-.01.06.01.24 0 .38z" />
                        </svg>
                        Signals
                      </Button>
                    </a>
                  )}
                  {telegramChat?.url && (
                    <a
                      href={telegramChat.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <Button variant="outline" size="lg">
                        <svg
                          className="w-5 h-5 mr-2"
                          viewBox="0 0 24 24"
                          fill="currentColor"
                        >
                          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.2-.08-.06-.19-.04-.27-.02-.12.02-1.96 1.25-5.54 3.66-.52.36-1 .53-1.42.52-.47-.01-1.37-.26-2.03-.48-.82-.27-1.47-.42-1.42-.88.03-.24.37-.49 1.02-.75 3.98-1.73 6.64-2.87 7.97-3.43 3.8-1.57 4.59-1.85 5.1-1.85.11 0 .37.03.53.17.14.12.18.28.2.45-.01.06.01.24 0 .38z" />
                        </svg>
                        Community
                      </Button>
                    </a>
                  )}
                  {twitter?.url && (
                    <a
                      href={twitter.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <Button variant="outline" size="lg">
                        <svg
                          className="w-5 h-5 mr-2"
                          viewBox="0 0 24 24"
                          fill="currentColor"
                        >
                          <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                        </svg>
                        X (Twitter)
                      </Button>
                    </a>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Disclaimer */}
            <div className="text-center">
              <p className="text-sm text-muted-foreground">
                {t("copy.disclaimer")}
              </p>
            </div>
          </div>
        </main>

        <Footer t={t} />
      </div>
    </>
  );
}
