"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import useSWR from "swr";
import {
  Settings,
  Link as LinkIcon,
  Server,
  RefreshCw,
  Power,
  Play,
  LogOut,
  AlertTriangle,
  Bell,
  Palette,
  Upload,
  Image,
  Type,
  Globe,
  Mail,
  FileText,
  Terminal,
  Wrench,
  ChevronDown,
  ChevronRight,
  X,
  Info,
  AlertCircle,
  CheckCircle,
  Monitor,
  Cpu,
  HardDrive,
  GitBranch,
  Clock,
  Save,
  MessageCircle,
  Users,
  Lock,
  Send,
  Eye,
  EyeOff,
  Hash,
} from "lucide-react";

import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTranslation, type Locale } from "@/lib/i18n";

// Loading skeleton for CardSkeleton (used by SystemInfoPanel)
function CardSkeleton() {
  return (
    <div className="h-32 bg-muted/30 rounded-lg animate-pulse" />
  );
}

// ============================================================================
// Config Field Renderer - renders fields from /api/admin/config/sections
// ============================================================================
interface ConfigField {
  path: string;
  label: string;
  type: string;
  value: any;
  description?: string;
  options?: string[];
  sensitive?: boolean;
}

interface ConfigSection {
  id: string;
  title: string;
  description: string;
  fields: ConfigField[];
}

function ConfigFieldInput({
  field,
  onSave,
}: {
  field: ConfigField;
  onSave: (path: string, value: any) => void;
}) {
  const [localValue, setLocalValue] = useState(field.value);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setLocalValue(field.value);
    setDirty(false);
  }, [field.value]);

  const handleChange = (newValue: any) => {
    setLocalValue(newValue);
    setDirty(true);
  };

  const handleSave = () => {
    if (!dirty) return;
    let val = localValue;
    if (field.type === "number") {
      val = Number(val);
      if (isNaN(val)) return;
    }
    onSave(field.path, val);
    setDirty(false);
  };

  const inputClass =
    "w-full px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm transition-colors" +
    (dirty ? " border-yellow-500/50" : "");

  if (field.sensitive) {
    return (
      <div>
        <label className="text-sm text-muted-foreground flex items-center gap-1.5">
          {field.label}
          {field.description && (
            <span title={field.description} className="cursor-help">
              <Info className="h-3 w-3 text-muted-foreground/50" />
            </span>
          )}
        </label>
        <input
          type="password"
          className={inputClass + " mt-1"}
          value="********"
          disabled
        />
        <p className="text-xs text-muted-foreground mt-1">Set via ~/.env.algvex</p>
      </div>
    );
  }

  if (field.type === "boolean") {
    return (
      <div className="flex items-center justify-between py-1">
        <div>
          <label className="text-sm font-medium">{field.label}</label>
          {field.description && (
            <p className="text-xs text-muted-foreground">{field.description}</p>
          )}
        </div>
        <button
          onClick={() => {
            const newVal = !localValue;
            setLocalValue(newVal);
            onSave(field.path, newVal);
          }}
          className={`relative w-11 h-6 rounded-full transition-colors ${
            localValue ? "bg-primary" : "bg-muted-foreground/30"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow-sm ${
              localValue ? "translate-x-5" : ""
            }`}
          />
        </button>
      </div>
    );
  }

  if (field.type === "select" && field.options) {
    return (
      <div>
        <label className="text-sm text-muted-foreground flex items-center gap-1.5">
          {field.label}
          {field.description && (
            <span title={field.description} className="cursor-help">
              <Info className="h-3 w-3 text-muted-foreground/50" />
            </span>
          )}
        </label>
        <select
          className={inputClass + " mt-1"}
          value={localValue ?? ""}
          onChange={(e) => {
            setLocalValue(e.target.value);
            onSave(field.path, e.target.value);
          }}
        >
          {field.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // number or string
  return (
    <div>
      <label className="text-sm text-muted-foreground flex items-center gap-1.5">
        {field.label}
        {field.description && (
          <span title={field.description} className="cursor-help">
            <Info className="h-3 w-3 text-muted-foreground/50" />
          </span>
        )}
      </label>
      <div className="flex gap-2 mt-1">
        <input
          type={field.type === "number" ? "number" : "text"}
          step={field.type === "number" ? "any" : undefined}
          className={inputClass}
          value={localValue ?? ""}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={handleSave}
          onKeyDown={(e) => e.key === "Enter" && handleSave()}
        />
        {dirty && (
          <Button size="sm" onClick={handleSave} className="px-2 h-[38px]">
            <Save className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

function ConfigSectionCard({
  section,
  onSave,
  defaultOpen = false,
}: {
  section: ConfigSection;
  onSave: (path: string, value: any) => void;
  defaultOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <Card className="border-border/50">
      <CardHeader
        className="cursor-pointer select-none pb-3"
        onClick={() => setIsOpen(!isOpen)}
      >
        <CardTitle className="flex items-center justify-between text-base">
          <div>
            <span>{section.title}</span>
            <span className="text-xs text-muted-foreground font-normal ml-2">
              ({section.fields.length} fields)
            </span>
          </div>
          {isOpen ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </CardTitle>
        {!isOpen && section.description && (
          <p className="text-xs text-muted-foreground">{section.description}</p>
        )}
      </CardHeader>
      {isOpen && (
        <CardContent className="space-y-4 pt-0">
          {section.description && (
            <p className="text-xs text-muted-foreground pb-2 border-b border-border/30">
              {section.description}
            </p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {section.fields.map((field) => (
              <ConfigFieldInput key={field.path} field={field} onSave={onSave} />
            ))}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// ============================================================================
// Logs Viewer Component
// ============================================================================
function LogsViewer({ token }: { token: string }) {
  const [logs, setLogs] = useState("");
  const [loading, setLoading] = useState(false);
  const [lines, setLines] = useState(100);
  const [source, setSource] = useState<"journalctl" | "file">("journalctl");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/admin/service/logs?lines=${lines}&source=${source}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const data = await res.json();
      setLogs(data.logs || "No logs available");
    } catch {
      setLogs("Failed to fetch logs");
    }
    setLoading(false);
  }, [token, lines, source]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchLogs]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Terminal className="h-5 w-5" />
              服务日志
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={fetchLogs}
              disabled={loading}
              className="h-7 text-xs sm:hidden"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </Button>
          </div>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <select
              className="px-2 py-1 rounded bg-muted border border-border text-xs"
              value={source}
              onChange={(e) => setSource(e.target.value as "journalctl" | "file")}
            >
              <option value="journalctl">journalctl</option>
              <option value="file">Log File</option>
            </select>
            <select
              className="px-2 py-1 rounded bg-muted border border-border text-xs"
              value={lines}
              onChange={(e) => setLines(Number(e.target.value))}
            >
              <option value={50}>50 lines</option>
              <option value={100}>100 lines</option>
              <option value={200}>200 lines</option>
              <option value={500}>500 lines</option>
            </select>
            <Button
              variant={autoRefresh ? "default" : "outline"}
              size="sm"
              onClick={() => setAutoRefresh(!autoRefresh)}
              className="h-7 text-xs"
            >
              {autoRefresh ? "Auto" : "Manual"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={fetchLogs}
              disabled={loading}
              className="h-7 text-xs hidden sm:flex"
            >
              <RefreshCw className={`h-3 w-3 mr-1 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="bg-black/80 rounded-lg p-2 sm:p-4 max-h-[400px] sm:max-h-[600px] overflow-auto font-mono text-[10px] sm:text-xs text-green-400 whitespace-pre-wrap leading-relaxed">
          {logs}
          <div ref={logsEndRef} />
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Diagnostics Component
// ============================================================================
function DiagnosticsPanel({ token }: { token: string }) {
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [expandedChecks, setExpandedChecks] = useState<Set<number>>(new Set());

  const runDiagnostics = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/system/diagnostics", {
        headers: { Authorization: `Bearer ${token}` },
      });
      setDiagnostics(await res.json());
    } catch {
      setDiagnostics({ checks: [{ name: "Connection", status: "fail", message: "Failed to connect to backend" }] });
    }
    setLoading(false);
  };

  useEffect(() => {
    runDiagnostics();
  }, []);

  const statusIcon = (status: string) => {
    if (status === "pass") return <CheckCircle className="h-4 w-4 text-green-500" />;
    if (status === "warn") return <AlertTriangle className="h-4 w-4 text-yellow-500" />;
    return <X className="h-4 w-4 text-red-500" />;
  };

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-lg">
          <div className="flex items-center gap-2">
            <Wrench className="h-5 w-5" />
            系统诊断
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={runDiagnostics}
            disabled={loading}
            className="h-7 text-xs"
          >
            <RefreshCw className={`h-3 w-3 mr-1 ${loading ? "animate-spin" : ""}`} />
            重新运行
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!diagnostics ? (
          <div className="text-sm text-muted-foreground">诊断运行中...</div>
        ) : (
          <div className="space-y-2">
            {diagnostics.checks?.map((check: any, idx: number) => (
              <div
                key={idx}
                className={`flex items-center gap-3 p-3 rounded-lg border ${
                  check.status === "pass"
                    ? "bg-green-500/5 border-green-500/20"
                    : check.status === "warn"
                    ? "bg-yellow-500/5 border-yellow-500/20"
                    : "bg-red-500/5 border-red-500/20"
                }`}
              >
                {statusIcon(check.status)}
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium">{check.name}</span>
                  <p
                    className={`text-xs text-muted-foreground cursor-pointer hover:text-foreground/70 transition-colors ${expandedChecks.has(idx) ? 'break-words' : 'truncate'}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedChecks(prev => {
                        const next = new Set(prev);
                        next.has(idx) ? next.delete(idx) : next.add(idx);
                        return next;
                      });
                    }}
                  >
                    {check.message}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// 系统信息 Component
// ============================================================================
function SystemInfoPanel({ token }: { token: string }) {
  const [info, setInfo] = useState<any>(null);
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch("/api/admin/system/info", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.json())
      .then(setInfo)
      .catch(() => {});
  }, [token]);

  if (!info) return <CardSkeleton />;

  const items = [
    { icon: Monitor, label: "系统版本", value: info.system_version || "N/A" },
    { icon: Settings, label: "策略模式", value: "Prism" },
    { icon: Cpu, label: "Python", value: info.python_version || "N/A" },
    { icon: HardDrive, label: "NautilusTrader", value: info.nautilus_version || "N/A" },
    { icon: Monitor, label: "Web 版本", value: info.web_version || "N/A" },
    { icon: GitBranch, label: "Git Branch", value: info.git_branch || "N/A" },
    { icon: Clock, label: "上次提交", value: info.git_commit ? `${info.git_commit} (${info.git_commit_date?.split(" ")[0] || ""})` : "N/A" },
    { icon: Server, label: "服务", value: info.service_name || "N/A" },
    { icon: Settings, label: "环境", value: info.active_env || "production" },
    { icon: Monitor, label: "Path", value: info.algvex_path || "N/A" },
  ];

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Monitor className="h-5 w-5" />
          系统信息
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {items.map(({ icon: Icon, label, value }) => (
            <div key={label} className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/30">
              <Icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p
                  className={`text-sm font-mono cursor-pointer hover:text-foreground/70 transition-colors ${expandedItems.has(label) ? 'break-all' : 'truncate'}`}
                  onClick={() => setExpandedItems(prev => {
                    const next = new Set(prev);
                    next.has(label) ? next.delete(label) : next.add(label);
                    return next;
                  })}
                >
                  {value}
                </p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Main Dashboard
// ============================================================================
export default function AdminDashboard() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("strategy");
  const [config, setConfig] = useState<any>(null);
  const [configSections, setConfigSections] = useState<ConfigSection[]>([]);
  const [socialLinks, setSocialLinks] = useState<any[]>([]);
  const [copyLinks, setCopyLinks] = useState<any[]>([]);
  const [siteSettings, setSiteSettings] = useState<Record<string, string>>({});
  const [telegramConfig, setTelegramConfig] = useState<Record<string, string>>({});
  const [telegramLinks, setTelegramLinks] = useState<Record<string, string>>({});
  const [telegramSaving, setTelegramSaving] = useState(false);
  const [showToken, setShowToken] = useState<Record<string, boolean>>({});
  const [pendingRestart, setPendingRestart] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Authentication check
  useEffect(() => {
    const storedToken = localStorage.getItem("admin_token");
    if (!storedToken) {
      router.replace("/admin");
      return;
    }

    // Verify token
    fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${storedToken}` },
    })
      .then((res) => {
        if (res.ok) {
          setToken(storedToken);
        } else {
          localStorage.removeItem("admin_token");
          router.replace("/admin");
        }
      })
      .catch(() => {
        router.replace("/admin");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [router]);

  // Fetch service status
  const { data: serviceStatus, mutate: refetchStatus } = useSWR(
    token ? ["/api/admin/service/status", token] : null,
    ([url, t]) =>
      fetch(url, { headers: { Authorization: `Bearer ${t}` } }).then((r) => r.json()),
    { refreshInterval: 5000 }
  );

  // All monitoring data (performance, trades, signals, layers, safety events, etc.)
  // has been moved to public frontend pages (/dashboard, /quality, /performance).
  // Admin only handles configuration and control operations.

  // Fetch config sections (structured) and raw config
  useEffect(() => {
    if (token) {
      // Structured sections for Strategy tab
      fetch("/api/admin/config/sections", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then((data) => setConfigSections(data.sections || []))
        .catch(console.error);

      // Raw config
      fetch("/api/admin/config", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then(setConfig)
        .catch(console.error);

      fetch("/api/admin/social-links", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then((links: any[]) => {
          setSocialLinks(links);
          // Initialize telegram invite links from DB values
          const tg = links.find((l: any) => l.platform === "telegram");
          const tgChat = links.find((l: any) => l.platform === "telegram_chat");
          setTelegramLinks({
            telegram: tg?.url || "",
            telegram_chat: tgChat?.url || "",
          });
        })
        .catch(console.error);

      fetch("/api/admin/telegram-config", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then(setTelegramConfig)
        .catch(console.error);

      fetch("/api/admin/copy-trading", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then(setCopyLinks)
        .catch(console.error);

      fetch("/api/admin/settings", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => r.json())
        .then(setSiteSettings)
        .catch(console.error);
    }
  }, [token]);

  const showMessage = useCallback((type: "success" | "error", text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 4000);
  }, []);

  const handle退出 = () => {
    localStorage.removeItem("admin_token");
    router.replace("/admin");
  };

  const handleServiceControl = async (action: "restart" | "stop" | "start") => {
    if (!token) return;

    const confirmed = window.confirm(
      `Are you sure you want to ${action} the trading service?`
    );
    if (!confirmed) return;

    try {
      const res = await fetch("/api/admin/service/control", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ action, confirm: true }),
      });
      const data = await res.json();

      if (data.success) {
        showMessage("success", data.message);
        setPendingRestart(false);
        refetchStatus();
      } else {
        showMessage("error", data.message || "Failed");
      }
    } catch (e: any) {
      showMessage("error", e.message);
    }
  };

  const handleConfigSave = async (path: string, value: any) => {
    if (!token) return;

    try {
      const res = await fetch("/api/admin/config", {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ path, value }),
      });
      const data = await res.json();

      if (data.success) {
        showMessage("success", `Updated ${path}`);
        setPendingRestart(true);
        // Update local sections state
        setConfigSections((prev) =>
          prev.map((section) => ({
            ...section,
            fields: section.fields.map((field) =>
              field.path === path ? { ...field, value } : field
            ),
          }))
        );
      } else {
        showMessage("error", "Failed to update config");
      }
    } catch (e: any) {
      showMessage("error", e.message);
    }
  };

  const handleSocialLinkSave = async (platform: string, url: string) => {
    if (!token) return;

    try {
      await fetch(`/api/admin/social-links/${platform}`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ platform, url, enabled: !!url }),
      });
      showMessage("success", `Updated ${platform} link`);
    } catch (e: any) {
      showMessage("error", e.message);
    }
  };

  const handleTelegramConfigSave = async () => {
    if (!token) return;
    setTelegramSaving(true);
    try {
      // 1) Save env vars (tokens + chat IDs)
      const res = await fetch("/api/admin/telegram-config", {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(telegramConfig),
      });
      const data = await res.json();

      // 2) Save invite links to social_links DB
      const linkSaves = Object.entries(telegramLinks).map(([platform, url]) =>
        fetch(`/api/admin/social-links/${platform}`, {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ platform, url, enabled: !!url }),
        })
      );
      await Promise.all(linkSaves);

      if (data.success) {
        showMessage("success", "Telegram config & links saved");
        if (data.requires_restart) setPendingRestart(true);
        // Re-fetch to get freshly masked values
        const fresh = await fetch("/api/admin/telegram-config", {
          headers: { Authorization: `Bearer ${token}` },
        }).then((r) => r.json());
        setTelegramConfig(fresh);
        setShowToken({});
      } else {
        showMessage("error", data.detail || "Failed to save");
      }
    } catch (e: any) {
      showMessage("error", e.message);
    } finally {
      setTelegramSaving(false);
    }
  };

  const handleSiteSettingSave = async (key: string, value: string) => {
    if (!token) return;

    try {
      await fetch(`/api/admin/settings/${key}?value=${encodeURIComponent(value)}`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      setSiteSettings((prev) => ({ ...prev, [key]: value }));
      showMessage("success", `Updated ${key}`);
    } catch (e: any) {
      showMessage("error", e.message);
    }
  };

  const handleFileUpload = async (type: "logo" | "favicon", file: File) => {
    if (!token) return;

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`/api/admin/upload/${type}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      const data = await res.json();
      if (data.success) {
        setSiteSettings((prev) => ({
          ...prev,
          [`${type}_url`]: data.url,
        }));
        showMessage("success", `${type} uploaded successfully`);
      } else {
        showMessage("error", data.detail || "Upload failed");
      }
    } catch (e: any) {
      showMessage("error", e.message);
    } finally {
      setUploading(false);
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen gradient-bg flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 mx-auto mb-4 rounded-xl bg-primary flex items-center justify-center animate-pulse">
            <span className="text-primary-foreground font-bold text-xl">A</span>
          </div>
          <p className="text-muted-foreground">加载中...</p>
        </div>
      </div>
    );
  }

  // Not authenticated
  if (!token) {
    return null;
  }

  const tabs = [
    { id: "strategy", label: "策略", icon: Settings },
    { id: "system", label: "系统", icon: Terminal },
    { id: "links", label: "链接", icon: LinkIcon },
    { id: "site", label: "站点", icon: Palette },
  ];

  return (
    <>
      <Head>
        <title>管理后台 - {siteSettings.site_name || "AlgVex"}</title>
      </Head>

      <div className="min-h-screen gradient-bg">
        {/* Main Site Header */}
        <Header locale={locale} t={t} />

        {/* Admin Toolbar - positioned below the main navbar */}
        <div className="fixed top-20 inset-x-0 z-40 px-2 sm:px-4">
          <div className="max-w-7xl mx-auto">
            <div className="flex items-center justify-between px-3 sm:px-4 py-2 bg-background/80 backdrop-blur-xl border border-border/40 rounded-xl">
              <div className="flex items-center gap-2 sm:gap-3 min-w-0">
                <span className="text-xs sm:text-sm font-semibold text-primary whitespace-nowrap">Admin</span>
                {serviceStatus?.running && (
                  <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-green-500/10 text-green-500 text-xs">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                    <span className="hidden sm:inline">Live</span>
                  </span>
                )}
                {serviceStatus?.uptime && (
                  <span className="text-xs text-muted-foreground hidden md:inline">
                    Uptime: {serviceStatus.uptime}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1 sm:gap-2">
                <Button variant="ghost" size="icon" className="relative h-8 w-8">
                  <Bell className="h-4 w-4" />
                  {pendingRestart && (
                    <span className="absolute top-1 right-1 w-2 h-2 bg-yellow-500 rounded-full" />
                  )}
                </Button>
                <Button variant="ghost" size="icon" onClick={handle退出} className="h-8 w-8 sm:w-auto sm:px-3">
                  <LogOut className="h-4 w-4 sm:mr-2" />
                  <span className="hidden sm:inline text-sm">退出</span>
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* Message Toast */}
        {message && (
          <div
            className={`fixed top-32 left-4 right-4 sm:left-auto sm:right-4 p-3 sm:p-4 rounded-lg z-50 shadow-lg sm:max-w-sm ${
              message.type === "success"
                ? "bg-green-500/10 text-green-500 border border-green-500/30"
                : "bg-red-500/10 text-red-500 border border-red-500/30"
            }`}
          >
            <div className="flex items-center gap-2">
              {message.type === "success" ? (
                <CheckCircle className="h-4 w-4 flex-shrink-0" />
              ) : (
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
              )}
              <span className="text-sm">{message.text}</span>
            </div>
          </div>
        )}

        {/* Main Content - pt-36 accounts for main navbar (h-14 + top-4) + admin toolbar */}
        <main className="container mx-auto px-3 sm:px-4 pt-36 pb-6">
          {/* Tabs */}
          <div className="flex gap-1.5 sm:gap-2 mb-4 sm:mb-6 overflow-x-auto pb-2 -mx-1 px-1 scrollbar-hide">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <Button
                  key={tab.id}
                  variant={activeTab === tab.id ? "default" : "outline"}
                  onClick={() => setActiveTab(tab.id)}
                  className="whitespace-nowrap text-xs sm:text-sm px-2.5 sm:px-3"
                  size="sm"
                >
                  <Icon className="h-3.5 w-3.5 sm:h-4 sm:w-4 mr-1 sm:mr-2" />
                  <span className="hidden sm:inline">{tab.label}</span>
                  <span className="sm:hidden">{tab.label.length > 8 ? tab.label.slice(0, 6) : tab.label}</span>
                </Button>
              );
            })}
          </div>

          {/* Pending Restart Banner */}
          {pendingRestart && (
            <div className="mb-4 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30 flex items-center gap-3">
              <AlertTriangle className="h-4 w-4 text-yellow-500 flex-shrink-0" />
              <span className="text-sm text-yellow-500 flex-1">
                配置已更改。重启服务以生效。
              </span>
              <Button size="sm" onClick={() => handleServiceControl("restart")} className="h-7 text-xs">
                立即重启
              </Button>
            </div>
          )}

          {/* ================================================================ */}
          {/* Strategy Tab - Full Configuration */}
          {/* ================================================================ */}
          {activeTab === "strategy" && (
            <div className="space-y-4">
              {/* Service Status (compact) - kept here for quick access */}
              <Card className="border-border/50">
                <CardContent className="py-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-2 mr-4">
                      <div
                        className={`h-3 w-3 rounded-full ${
                          serviceStatus?.running ? "bg-green-500 animate-pulse" : "bg-red-500"
                        }`}
                      />
                      <span className="text-sm font-medium">
                        {serviceStatus?.running ? "Running" : "Stopped"}
                      </span>
                      {serviceStatus?.uptime && (
                        <span className="text-xs text-muted-foreground">({serviceStatus.uptime})</span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" onClick={() => handleServiceControl("restart")} className="h-8">
                        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                        Restart
                      </Button>
                      {serviceStatus?.running ? (
                        <Button variant="outline" size="sm" onClick={() => handleServiceControl("stop")} className="h-8">
                          <Power className="h-3.5 w-3.5 mr-1.5" />
                          Stop
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => handleServiceControl("start")} className="h-8">
                          <Play className="h-3.5 w-3.5 mr-1.5" />
                          Start
                        </Button>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>

              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                  <h2 className="text-lg sm:text-xl font-bold">策略配置</h2>
                  <p className="text-xs sm:text-sm text-muted-foreground">
                    所有参数来自 configs/base.yaml。更改需重启服务。
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="self-start sm:self-auto"
                  onClick={() => {
                    // Reload sections from server
                    fetch("/api/admin/config/sections", {
                      headers: { Authorization: `Bearer ${token}` },
                    })
                      .then((r) => r.json())
                      .then((data) => {
                        setConfigSections(data.sections || []);
                        showMessage("success", "Configuration reloaded");
                      })
                      .catch(() => showMessage("error", "Failed to reload"));
                  }}
                >
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Reload
                </Button>
              </div>

              {configSections.length === 0 ? (
                <Card className="border-border/50">
                  <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    Loading configuration sections...
                  </CardContent>
                </Card>
              ) : (
                configSections.map((section, idx) => (
                  <ConfigSectionCard
                    key={section.id}
                    section={section}
                    onSave={handleConfigSave}
                    defaultOpen={idx < 3}
                  />
                ))
              )}
            </div>
          )}

          {/* ================================================================ */}
          {/* System Tab - Logs + Diagnostics + 系统信息 */}
          {/* ================================================================ */}
          {activeTab === "system" && (
            <div className="space-y-6">
              {/* Service Control (compact) */}
              <Card className="border-border/50">
                <CardContent className="py-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="flex items-center gap-2 mr-4">
                      <div
                        className={`h-3 w-3 rounded-full ${
                          serviceStatus?.running ? "bg-green-500 animate-pulse" : "bg-red-500"
                        }`}
                      />
                      <span className="text-sm font-medium">
                        {serviceStatus?.running ? "Running" : "Stopped"}
                      </span>
                      {serviceStatus?.uptime && (
                        <span className="text-xs text-muted-foreground">({serviceStatus.uptime})</span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" onClick={() => handleServiceControl("restart")} className="h-8">
                        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                        Restart
                      </Button>
                      {serviceStatus?.running ? (
                        <Button variant="outline" size="sm" onClick={() => handleServiceControl("stop")} className="h-8">
                          <Power className="h-3.5 w-3.5 mr-1.5" />
                          Stop
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => handleServiceControl("start")} className="h-8">
                          <Play className="h-3.5 w-3.5 mr-1.5" />
                          Start
                        </Button>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 系统信息 */}
              <SystemInfoPanel token={token} />

              {/* Diagnostics */}
              <DiagnosticsPanel token={token} />

              {/* Logs */}
              <LogsViewer token={token} />
            </div>
          )}

          {/* ================================================================ */}
          {/* Links Tab */}
          {/* ================================================================ */}
          {activeTab === "links" && (
            <div className="space-y-6">
              {/* ── Telegram 配置 ── */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Send className="h-5 w-5" />
                    Telegram 配置
                  </CardTitle>
                  <p className="text-sm text-muted-foreground mt-1">
                    Manage bot tokens, chat IDs, and invite links.
                    Changes to tokens require a trading service restart.
                  </p>
                </CardHeader>
                <CardContent className="space-y-6">

                  {/* ---- 私有控制机器人 ---- */}
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/50 space-y-3">
                    <div className="flex items-center gap-2 mb-1">
                      <Lock className="h-4 w-4 text-primary" />
                      <span className="font-semibold">私有控制机器人</span>
                      <span className="text-xs text-muted-foreground">(Admin only)</span>
                    </div>
                    <p className="text-xs text-muted-foreground -mt-1">
                      Receives system alerts, heartbeat, error logs. Supports interactive commands.
                    </p>
                    {/* Bot Token */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Bot Token</label>
                      <div className="flex gap-2">
                        <input
                          type={showToken["ctrl_token"] ? "text" : "password"}
                          className="flex-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm font-mono"
                          value={telegramConfig.TELEGRAM_BOT_TOKEN || ""}
                          placeholder="123456:ABCdefGhIJKlmNoPQRsTUVwxyz"
                          onChange={(e) => setTelegramConfig((p) => ({ ...p, TELEGRAM_BOT_TOKEN: e.target.value }))}
                        />
                        <button
                          type="button"
                          className="px-2 text-muted-foreground hover:text-foreground"
                          onClick={() => setShowToken((p) => ({ ...p, ctrl_token: !p.ctrl_token }))}
                        >
                          {showToken["ctrl_token"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </button>
                      </div>
                    </div>
                    {/* Chat ID */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Chat ID</label>
                      <div className="flex items-center gap-2">
                        <Hash className="h-4 w-4 text-muted-foreground" />
                        <input
                          type="text"
                          className="flex-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm font-mono"
                          value={telegramConfig.TELEGRAM_CHAT_ID || ""}
                          placeholder="123456789"
                          onChange={(e) => setTelegramConfig((p) => ({ ...p, TELEGRAM_CHAT_ID: e.target.value }))}
                        />
                      </div>
                    </div>
                  </div>

                  {/* ---- 通知频道 ---- */}
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/50 space-y-3">
                    <div className="flex items-center gap-2 mb-1">
                      <MessageCircle className="h-4 w-4 text-green-500" />
                      <span className="font-semibold">通知频道</span>
                      <span className="text-xs text-muted-foreground">(Subscribers)</span>
                    </div>
                    <p className="text-xs text-muted-foreground -mt-1">
                      Broadcasts trade signals, position updates, daily/weekly reports.
                    </p>
                    {/* Bot Token */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Bot Token</label>
                      <div className="flex gap-2">
                        <input
                          type={showToken["notif_token"] ? "text" : "password"}
                          className="flex-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm font-mono"
                          value={telegramConfig.TELEGRAM_NOTIFICATION_BOT_TOKEN || ""}
                          placeholder="123456:ABCdefGhIJKlmNoPQRsTUVwxyz"
                          onChange={(e) => setTelegramConfig((p) => ({ ...p, TELEGRAM_NOTIFICATION_BOT_TOKEN: e.target.value }))}
                        />
                        <button
                          type="button"
                          className="px-2 text-muted-foreground hover:text-foreground"
                          onClick={() => setShowToken((p) => ({ ...p, notif_token: !p.notif_token }))}
                        >
                          {showToken["notif_token"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </button>
                      </div>
                    </div>
                    {/* Chat ID */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Chat ID</label>
                      <div className="flex items-center gap-2">
                        <Hash className="h-4 w-4 text-muted-foreground" />
                        <input
                          type="text"
                          className="flex-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm font-mono"
                          value={telegramConfig.TELEGRAM_NOTIFICATION_CHAT_ID || ""}
                          placeholder="-1001234567890"
                          onChange={(e) => setTelegramConfig((p) => ({ ...p, TELEGRAM_NOTIFICATION_CHAT_ID: e.target.value }))}
                        />
                      </div>
                    </div>
                    {/* Invite Link (stored in social_links, saved with Save button) */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Public Invite Link (shown on website)</label>
                      <input
                        type="text"
                        className="w-full px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm"
                        value={telegramLinks.telegram || ""}
                        placeholder="https://t.me/AlgVex"
                        onChange={(e) => setTelegramLinks((p) => ({ ...p, telegram: e.target.value }))}
                      />
                    </div>
                  </div>

                  {/* ---- 社群组 ---- */}
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/50 space-y-3">
                    <div className="flex items-center gap-2 mb-1">
                      <Users className="h-4 w-4 text-blue-500" />
                      <span className="font-semibold">社群组</span>
                    </div>
                    <p className="text-xs text-muted-foreground -mt-1">
                      Public discussion group for users. No bot token needed.
                    </p>
                    {/* Invite Link (stored in social_links, saved with Save button) */}
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">Public Invite Link (shown on website)</label>
                      <input
                        type="text"
                        className="w-full px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm"
                        value={telegramLinks.telegram_chat || ""}
                        placeholder="https://t.me/AlgVex_Community"
                        onChange={(e) => setTelegramLinks((p) => ({ ...p, telegram_chat: e.target.value }))}
                      />
                    </div>
                  </div>

                  {/* Save Button */}
                  <div className="flex items-center justify-between pt-2">
                    <p className="text-xs text-muted-foreground">
                      {telegramConfig.env_path && `Source: ${telegramConfig.env_path}`}
                    </p>
                    <Button
                      onClick={handleTelegramConfigSave}
                      disabled={telegramSaving}
                      className="gap-2"
                    >
                      {telegramSaving ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      保存 Telegram 配置
                    </Button>
                  </div>
                </CardContent>
              </Card>

              {/* ── Other 社交链接 ── */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle>社交链接</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {socialLinks
                    .filter((link) => link.platform !== "telegram" && link.platform !== "telegram_chat")
                    .map((link) => {
                      const labels: Record<string, string> = {
                        twitter: "Twitter / X",
                        discord: "Discord",
                        github: "GitHub",
                      };
                      const placeholders: Record<string, string> = {
                        twitter: "https://x.com/...",
                        discord: "https://discord.gg/...",
                        github: "https://github.com/...",
                      };
                      return (
                        <div key={link.platform} className="space-y-1 sm:space-y-0 sm:flex sm:items-center sm:gap-4">
                          <span className="block text-sm text-muted-foreground sm:w-40 sm:text-foreground">{labels[link.platform] || link.platform}</span>
                          <input
                            type="text"
                            className="w-full sm:flex-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none text-sm"
                            defaultValue={link.url || ""}
                            placeholder={placeholders[link.platform] || `https://...`}
                            onBlur={(e) => handleSocialLinkSave(link.platform, e.target.value)}
                          />
                        </div>
                      );
                    })}
                </CardContent>
              </Card>

              {/* ── 跟单链接 ── */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle>跟单链接</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {copyLinks.map((link) => (
                    <div
                      key={link.id}
                      className="p-4 rounded-lg bg-muted/30 border border-border/50"
                    >
                      <div className="flex items-center justify-between mb-3">
                        <span className="font-semibold">{link.name}</span>
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="rounded"
                            defaultChecked={link.enabled}
                            onChange={async (e) => {
                              await fetch(`/api/admin/copy-trading/${link.id}`, {
                                method: "PUT",
                                headers: {
                                  Authorization: `Bearer ${token}`,
                                  "Content-Type": "application/json",
                                },
                                body: JSON.stringify({ enabled: e.target.checked }),
                              });
                            }}
                          />
                          Enabled
                        </label>
                      </div>
                      <input
                        type="text"
                        className="w-full px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                        defaultValue={link.url || ""}
                        placeholder="Copy trading URL"
                        onBlur={async (e) => {
                          await fetch(`/api/admin/copy-trading/${link.id}`, {
                            method: "PUT",
                            headers: {
                              Authorization: `Bearer ${token}`,
                              "Content-Type": "application/json",
                            },
                            body: JSON.stringify({ url: e.target.value }),
                          });
                          showMessage("success", "Link updated");
                        }}
                      />
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          )}

          {/* ================================================================ */}
          {/* Site Settings Tab */}
          {/* ================================================================ */}
          {activeTab === "site" && (
            <div className="space-y-6">
              {/* Logo 与品牌 */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Image className="h-5 w-5" />
                    Logo 与品牌
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Logo Upload */}
                  <div>
                    <label className="text-sm text-muted-foreground mb-2 block">Site Logo</label>
                    <div className="flex items-start gap-4">
                      <div className="w-24 h-24 rounded-lg border-2 border-dashed border-border flex items-center justify-center bg-muted/30 overflow-hidden">
                        {siteSettings.logo_url ? (
                          <img
                            src={siteSettings.logo_url}
                            alt="Logo"
                            className="w-full h-full object-contain"
                          />
                        ) : (
                          <Image className="h-8 w-8 text-muted-foreground" />
                        )}
                      </div>
                      <div className="flex-1">
                        <input
                          type="file"
                          accept="image/*"
                          className="hidden"
                          id="logo-upload"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) handleFileUpload("logo", file);
                          }}
                        />
                        <label htmlFor="logo-upload">
                          <Button
                            variant="outline"
                            size="sm"
                            className="cursor-pointer"
                            disabled={uploading}
                            asChild
                          >
                            <span>
                              <Upload className="h-4 w-4 mr-2" />
                              {uploading ? "Uploading..." : "Upload Logo"}
                            </span>
                          </Button>
                        </label>
                        <p className="text-xs text-muted-foreground mt-2">
                          Recommended: 200x200px, PNG or SVG
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Favicon Upload */}
                  <div>
                    <label className="text-sm text-muted-foreground mb-2 block">Favicon</label>
                    <div className="flex items-start gap-4">
                      <div className="w-16 h-16 rounded-lg border-2 border-dashed border-border flex items-center justify-center bg-muted/30 overflow-hidden">
                        {siteSettings.favicon_url ? (
                          <img
                            src={siteSettings.favicon_url}
                            alt="Favicon"
                            className="w-full h-full object-contain"
                          />
                        ) : (
                          <Globe className="h-6 w-6 text-muted-foreground" />
                        )}
                      </div>
                      <div className="flex-1">
                        <input
                          type="file"
                          accept="image/*,.ico"
                          className="hidden"
                          id="favicon-upload"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) handleFileUpload("favicon", file);
                          }}
                        />
                        <label htmlFor="favicon-upload">
                          <Button
                            variant="outline"
                            size="sm"
                            className="cursor-pointer"
                            disabled={uploading}
                            asChild
                          >
                            <span>
                              <Upload className="h-4 w-4 mr-2" />
                              {uploading ? "Uploading..." : "Upload Favicon"}
                            </span>
                          </Button>
                        </label>
                        <p className="text-xs text-muted-foreground mt-2">
                          Recommended: 32x32px or 64x64px, ICO or PNG
                        </p>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 站点信息 */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Type className="h-5 w-5" />
                    站点信息
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="text-sm text-muted-foreground">Site Name</label>
                      <input
                        type="text"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                        defaultValue={siteSettings.site_name || "AlgVex"}
                        onBlur={(e) => handleSiteSettingSave("site_name", e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-sm text-muted-foreground">Tagline</label>
                      <input
                        type="text"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                        defaultValue={siteSettings.tagline || "算法驱动 Crypto Trading"}
                        onBlur={(e) => handleSiteSettingSave("tagline", e.target.value)}
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-sm text-muted-foreground">Site Description (SEO)</label>
                    <textarea
                      className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none resize-none"
                      rows={3}
                      defaultValue={
                        siteSettings.site_description ||
                        "双策略算法交易系统：Prism 3 维预判评分 + SRP 均值回归"
                      }
                      onBlur={(e) => handleSiteSettingSave("site_description", e.target.value)}
                    />
                  </div>
                </CardContent>
              </Card>

              {/* 联系信息 */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Mail className="h-5 w-5" />
                    联系信息
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="text-sm text-muted-foreground">Contact Email</label>
                      <input
                        type="email"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                        defaultValue={siteSettings.contact_email || ""}
                        placeholder="contact@algvex.com"
                        onBlur={(e) => handleSiteSettingSave("contact_email", e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-sm text-muted-foreground">Support Email</label>
                      <input
                        type="email"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                        defaultValue={siteSettings.support_email || ""}
                        placeholder="support@algvex.com"
                        onBlur={(e) => handleSiteSettingSave("support_email", e.target.value)}
                      />
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Legal */}
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <FileText className="h-5 w-5" />
                    法律与免责声明
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <label className="text-sm text-muted-foreground">Risk Disclaimer</label>
                    <textarea
                      className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none resize-none"
                      rows={4}
                      defaultValue={
                        siteSettings.risk_disclaimer ||
                        "Trading cryptocurrencies involves significant risk. Past performance does not guarantee future results. Trade responsibly."
                      }
                      onBlur={(e) => handleSiteSettingSave("risk_disclaimer", e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-sm text-muted-foreground">Copyright Text</label>
                    <input
                      type="text"
                      className="w-full mt-1 px-3 py-2 rounded-lg bg-muted border border-border focus:border-primary focus:outline-none"
                      defaultValue={siteSettings.copyright_text || "© 2025 AlgVex. All rights reserved."}
                      onBlur={(e) => handleSiteSettingSave("copyright_text", e.target.value)}
                    />
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </main>
      </div>
    </>
  );
}
