"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  Award,
  DollarSign,
  Package,
  MessageSquare,
  Shield,
  Crown,
  Mail,
  User,
  Loader2,
  Check,
  Brain,
  Trash2,
  Star,
  X,
  TrendingUp,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface TierBenefits {
  discount_pct: number;
  free_shipping_threshold: number | null;
  priority_support: boolean;
}

interface Profile {
  id: string;
  email: string;
  name: string;
  role: string;
  loyalty_tier: string;
  total_spend: number;
  member_since: string;
  order_count: number;
  review_count: number;
  tier_benefits: TierBenefits;
}

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

const TIER_COLORS: Record<string, { bg: string; text: string; accent: string }> = {
  bronze: { bg: "bg-orange-50 dark:bg-orange-500/15", text: "text-orange-800 dark:text-orange-300", accent: "#CD7F32" },
  silver: { bg: "bg-muted", text: "text-muted-foreground", accent: "#C0C0C0" },
  gold: { bg: "bg-amber-50 dark:bg-amber-500/15", text: "text-amber-800 dark:text-amber-300", accent: "#FFD700" },
};

const TIER_LABELS: Record<string, string> = {
  bronze: "青铜",
  silver: "白银",
  gold: "黄金",
};

const ROLE_COLORS: Record<string, string> = {
  customer: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  power_user: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  seller: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  admin: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300",
};

const ROLE_LABELS: Record<string, string> = {
  customer: "普通用户",
  power_user: "高级会员",
  seller: "商家",
  admin: "管理员",
};

const TIER_THRESHOLDS: Record<string, { next: string; amount: number } | null> = {
  bronze: { next: "白银", amount: 1000 },
  silver: { next: "黄金", amount: 3000 },
  gold: null,
};

function formatCurrency(amount: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(amount);
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length <= 1) return (parts[0] ?? "").slice(0, 2);
  return parts
    .map((part) => part[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

function formatRole(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function formatTier(tier: string): string {
  return TIER_LABELS[tier] ?? tier;
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [memories, setMemories] = useState<
    { id: string; category: string; content: string; importance: number; created_at: string }[]
  >([]);
  const [memoriesLoading, setMemoriesLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadProfile = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getProfile();
      setProfile(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "个人资料加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMemories = useCallback(async () => {
    setMemoriesLoading(true);
    try {
      const rows = await api.getUserMemories();
      setMemories(rows ?? []);
    } catch {
      // 非致命错误 —— 记忆区域保持为空即可
    } finally {
      setMemoriesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) {
      loadProfile();
      loadMemories();
    }
  }, [user, loadProfile, loadMemories]);

  async function handleDeleteMemory(id: string) {
    setDeletingId(id);
    try {
      await api.deleteUserMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } finally {
      setDeletingId(null);
    }
  }

  if (authLoading) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <User className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">个人中心</h1>
              <p className="text-sm text-muted-foreground">
                您的账号信息与会员等级
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">
              正在加载个人资料…
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!loading && !error && profile && (
          <div className="space-y-8">
            {/* 资料头卡 */}
            <Card>
              <CardContent className="pt-6">
                <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
                  {/* 头像 */}
                  <div
                    className="flex size-20 shrink-0 items-center justify-center rounded-full text-2xl font-bold text-white"
                    style={{ backgroundColor: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  >
                    {getInitials(profile.name)}
                  </div>

                  <div className="flex-1 text-center sm:text-left">
                    <div className="flex flex-col items-center gap-2 sm:flex-row">
                      <h2 className="text-xl font-bold text-foreground">
                        {profile.name}
                      </h2>
                      <Badge
                        className={`${ROLE_COLORS[profile.role] ?? "bg-muted text-muted-foreground"} border-0`}
                      >
                        {profile.role === "admin" && (
                          <Shield className="mr-1 size-3" />
                        )}
                        {formatRole(profile.role)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {profile.email}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      注册于 {formatDate(profile.member_since)}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* 统计卡片 */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* 会员等级 */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    会员等级
                  </CardTitle>
                  <Crown
                    className="size-4"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  />
                </CardHeader>
                <CardContent>
                  <div
                    className="text-2xl font-bold"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  >
                    {formatTier(profile.loyalty_tier)}
                  </div>
                </CardContent>
              </Card>

              {/* 累计消费 */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    累计消费
                  </CardTitle>
                  <DollarSign className="size-4 text-emerald-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {formatCurrency(profile.total_spend)}
                  </div>
                </CardContent>
              </Card>

              {/* 订单数 */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    订单数
                  </CardTitle>
                  <Package className="size-4 text-sky-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {profile.order_count}
                  </div>
                </CardContent>
              </Card>

              {/* 评价数 */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    评价数
                  </CardTitle>
                  <MessageSquare className="size-4 text-amber-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {profile.review_count}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* 会员权益 */}
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Award
                    className="size-5"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  />
                  <CardTitle className="text-base font-semibold text-foreground">
                    会员权益
                  </CardTitle>
                  <Badge
                    className={`${TIER_COLORS[profile.loyalty_tier]?.bg ?? "bg-muted"} ${TIER_COLORS[profile.loyalty_tier]?.text ?? "text-muted-foreground"} border-0`}
                  >
                    {formatTier(profile.loyalty_tier)}会员
                  </Badge>
                </div>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                <div className="space-y-4">
                  {/* 折扣 */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <DollarSign className="size-4 text-muted-foreground" />
                      <span>下单折扣</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.tier_benefits.discount_pct > 0
                        ? `全部订单 ${profile.tier_benefits.discount_pct}% 折扣`
                        : "暂不可用"}
                    </span>
                  </div>

                  {/* 免运费 */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Package className="size-4 text-muted-foreground" />
                      <span>免运费</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.tier_benefits.free_shipping_threshold != null
                        ? `订单满 ${formatCurrency(profile.tier_benefits.free_shipping_threshold)} 免运费`
                        : "暂不可用"}
                    </span>
                  </div>

                  {/* 优先客服 */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4 text-muted-foreground" />
                      <span>优先客服</span>
                    </div>
                    <span className="flex items-center gap-1 text-sm font-medium">
                      {profile.tier_benefits.priority_support ? (
                        <>
                          <Check className="size-4 text-emerald-500" />
                          <span className="text-emerald-700">已包含</span>
                        </>
                      ) : (
                        <>
                          <X className="size-4 text-muted-foreground" />
                          <span className="text-muted-foreground">暂不可用</span>
                        </>
                      )}
                    </span>
                  </div>

                  {/* 升级进度 */}
                  {TIER_THRESHOLDS[profile.loyalty_tier] && (
                    <>
                      <Separator />
                      <div className="flex items-start gap-2">
                        <TrendingUp className="mt-0.5 size-4 text-primary" />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-muted-foreground">
                            升级进度：距 {TIER_THRESHOLDS[profile.loyalty_tier]!.next}
                          </p>
                          {(() => {
                            const threshold = TIER_THRESHOLDS[profile.loyalty_tier]!.amount;
                            const remaining = Math.max(0, threshold - profile.total_spend);
                            const progress = Math.min(100, (profile.total_spend / threshold) * 100);
                            return (
                              <>
                                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
                                  <div
                                    className="h-full rounded-full bg-primary transition-all"
                                    style={{ width: `${progress}%` }}
                                  />
                                </div>
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {remaining > 0
                                    ? `再消费 ${formatCurrency(remaining)} 即可升级至 ${TIER_THRESHOLDS[profile.loyalty_tier]!.next}`
                                    : `您已满足升级至 ${TIER_THRESHOLDS[profile.loyalty_tier]!.next} 的条件！`}
                                </p>
                              </>
                            );
                          })()}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* 账号信息 */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-semibold text-foreground">
                  账号信息
                </CardTitle>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Mail className="size-4 text-muted-foreground" />
                      <span>邮箱</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.email}
                    </span>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4 text-muted-foreground" />
                      <span>角色</span>
                    </div>
                    <Badge
                      className={`${ROLE_COLORS[profile.role] ?? "bg-muted text-muted-foreground"} border-0`}
                    >
                      {formatRole(profile.role)}
                    </Badge>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <User className="size-4 text-muted-foreground" />
                      <span>用户 ID</span>
                    </div>
                    <span className="font-mono text-xs text-muted-foreground">
                      {profile.id.slice(0, 8)}...{profile.id.slice(-4)}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* AI 记忆 */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Brain className="size-5 text-primary" />
                    <CardTitle className="text-base font-semibold text-foreground">
                      AI 记忆
                    </CardTitle>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    智能体记住的关于您的信息
                  </span>
                </div>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                {memoriesLoading ? (
                  <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    <span>正在加载记忆…</span>
                  </div>
                ) : memories.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border py-8 text-center">
                    <Brain className="mx-auto mb-2 size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">
                      暂无记忆。与商品或评价智能体对话即可逐步建立您的画像。
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {memories.map((memory) => (
                      <div
                        key={memory.id}
                        className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3"
                      >
                        <div className="flex-1 space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <Badge
                              className="border-0 bg-sky-100 text-sky-700 text-xs"
                            >
                              {memory.category}
                            </Badge>
                            <span className="flex items-center gap-0.5" title={`重要性：${memory.importance}/10`}>
                              {Array.from({ length: Math.min(5, Math.ceil(memory.importance / 2)) }).map((_, i) => (
                                <Star key={i} className="size-3 fill-amber-400 text-amber-400" />
                              ))}
                            </span>
                          </div>
                          <p className="text-sm text-foreground">{memory.content}</p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(memory.created_at)}
                          </p>
                        </div>
                        <button
                          onClick={() => handleDeleteMemory(memory.id)}
                          disabled={deletingId === memory.id}
                          className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                          title="删除该记忆"
                        >
                          {deletingId === memory.id ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <Trash2 className="size-4" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
