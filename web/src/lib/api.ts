import { returnOperationSchema, type ReturnOperation } from "./return-operation";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly data: Record<string, unknown>) {
    super(typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : `接口请求失败（${status}）`);
  }
}

/**
 * API 调用的基础地址。默认为空：浏览器与自身同源通信，
 * 由 `src/app/api/[...path]/route.ts` 转发到编排器，因此后端地址不会被
 * 编译进这个产物包。只有在需要绕过该代理、直连某个编排器时才设置
 * NEXT_PUBLIC_API_URL——这样可行，但也会把跨域资源共享（CORS）问题一并
 * 带回来。
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

/**
 * 把服务端下发的 `/api/...` 路径补全为绝对地址，供 `fetch` 之外的地方使用
 * ——例如 <a> 的 href 或 `window.open`，在这些场景下相对路径会基于当前路由
 * 而非站点根解析。
 */
export function apiUrl(path: string): string {
  return path.startsWith("/api") ? `${API_URL}${path}` : path;
}

export interface Address {
  name?: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  phone?: string;
}

export interface CartItem {
  id: string;
  product_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  quantity: number;
  subtotal: number;
  image_url?: string;
  in_stock?: boolean;
  available_qty?: number;
}

/** 通过 `event: step` SSE 帧流式推送的智能体时间线中的一个步骤。 */
/** 该步骤数据的来源——shared/agent_observability.py 的 StepRecorderMiddleware。 */
export interface StepProvenance {
  source: string;
  row_ids: string[];
}

export interface AgentStep {
  agent?: string;
  tool_name: string;
  tool_input?: unknown;
  tool_output?: unknown;
  status?: string;
  duration_ms?: number;
  provenance?: StepProvenance;
}

/** GroundingReport 中的一条已核验/未核验声明（shared/grounding/verifier.py::ClaimVerdict）。 */
export interface GroundingClaim {
  type: "product" | "order" | "bare_id" | "amount" | "tracking";
  id: string;
  status: "verified" | "price_mismatch" | "not_found" | "unverifiable";
  detail: string | null;
  source: "ledger" | "db" | null;
}

/**
 * 服务端对某条助手消息给出的事实核验（grounding）结论——其中有多少条
 * 商品/订单卡片声明是真的对照 Postgres 校验过，而不仅仅是通过了格式校验
 * （shared/grounding/middleware.py::_attach_report）。仅当 `GROUNDING_MODE`
 * 为 `annotate` 或 `enforce` 时存在（默认：annotate）。
 */
export interface GroundingReport {
  total: number;
  verified: number;
  unverified: number;
  claims: GroundingClaim[];
}

/** 某个模式支持哪些能力——来自 `GET /api/orchestration/modes`（orchestrator/modes/base.py::ModeCapabilities）。 */
export interface OrchestrationModeCapabilities {
  streams: boolean;
  supports_hitl: boolean;
  supports_checkpoints: boolean;
  is_graph: boolean;
}

/** `GET /api/orchestration/modes` 中的一条记录——`/api/chat` 的 `mode` 字段可选择的模式。 */
export interface OrchestrationMode {
  name: string;
  label: string;
  description: string;
  capabilities: OrchestrationModeCapabilities;
  default: boolean;
}

/** `POST /api/orchestration/compare` 中某个模式的结果。 */
export interface CompareModeResult {
  mode: string;
  label: string;
  text: string;
  latency_ms: number;
  agents_involved: string[];
  step_count: number;
  graph_mermaid: string | null;
  error: string | null;
}

export interface CompareResponse {
  message: string;
  results: CompareModeResult[];
}

export interface RunStep {
  step_index: number;
  tool_name: string;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  status: string;
  duration_ms: number;
}

export interface RunEntry {
  id: string;
  agent_name: string;
  user_email: string | null;
  user_name: string | null;
  input_summary: string | null;
  tokens_in: number;
  tokens_out: number;
  tool_calls_count: number;
  duration_ms: number;
  status: "success" | "error";
  trace_id: string | null;
  created_at: string;
  steps: RunStep[];
}

export interface CartResponse {
  id: string;
  items: CartItem[];
  item_count: number;
  subtotal: number;
  discount_amount: number;
  coupon_code: string | null;
  total: number;
  shipping_address: Address | null;
  billing_address: Address | null;
  billing_same_as_shipping: boolean;
}

// JWT 失效 → 清除登录态并跳转到 /login。幂等操作。
function handleUnauthorized() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("ecommerce_user");
  localStorage.removeItem("ecommerce_access_token");
  localStorage.removeItem("ecommerce_refresh_token");
  if (!window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

class ApiClient {
  private token: string | null = null;
  private refreshToken: string | null = null;
  // 只允许一个进行中的刷新——并发的多个 401 共用同一次网络请求。
  private inflightRefresh: Promise<string | null> | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  setRefreshToken(token: string | null) {
    this.refreshToken = token;
  }

  /**
   * 尝试用当前的 refresh_token 换取新的访问令牌。
   * 返回新的访问令牌；若无法刷新则返回 `null`——此时调用方应把用户
   * 跳转到 /login。
   */
  private async tryRefresh(): Promise<string | null> {
    if (!this.refreshToken) return null;
    if (this.inflightRefresh) return this.inflightRefresh;
    this.inflightRefresh = (async () => {
      try {
        const res = await fetch(`${API_URL}/api/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        });
        if (!res.ok) return null;
        const data = (await res.json()) as { access_token?: string };
        if (!data.access_token) return null;
        this.token = data.access_token;
        if (typeof window !== "undefined") {
          localStorage.setItem("ecommerce_access_token", data.access_token);
        }
        return data.access_token;
      } catch {
        return null;
      } finally {
        this.inflightRefresh = null;
      }
    })();
    return this.inflightRefresh;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    { allowRefresh = true }: { allowRefresh?: boolean } = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}${path}`, { ...options, headers });

    if (res.status === 401) {
      // 只重试一次：如果有刷新令牌，就用它换取新的访问令牌并重放该请求
      // 一次。这避免了长对话会话中「会话静默失效」这一问题。
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.request<T>(path, options, { allowRefresh: false });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("登录已过期——请重新登录。");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body);
    }

    return res.json();
  }

  // 认证
  signup(email: string, password: string, name: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
  }

  login(email: string, password: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  refresh(refreshToken: string) {
    return this.request<{ access_token: string }>("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  }

  // 对话
  chat(message: string, conversationId?: string, signal?: AbortSignal) {
    return this.request<{
      response: string;
      conversation_id: string;
      agents_involved: string[];
      message_id?: string;
      grounding?: GroundingReport | null;
    }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });
  }

  /**
   * 流式对话——读取 SSE 事件，并为每个文本增量调用 onChunk。
   * 流结束后返回会话元数据。
   *
   * 传入 `AbortSignal` 可在流中途取消（例如用户离开页面或点击「停止」）。
   * 遇到 401 时客户端会刷新一次并重试该流；若刷新失败则把用户跳转到
   * /login。
   */
  async chatStream(
    message: string,
    conversationId: string | undefined,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
    options: {
      allowRefresh?: boolean;
      onStep?: (step: AgentStep) => void;
      /**
       * 对任何不是 `step`/`metadata`/展示文本的 SSE 帧触发——目前包括来自
       * 非「tool」编排模式的 `node`、`handoff`、`checkpoint`、`request_info`、
       * `error`（见 `orchestrator/routes/chat.py`），以及 `run`
       * ——`{run_id, pending_approval}`，由每种模式在持久化之后发出，
       * 因为 run 的 id 就是此时创建的 `usage_logs` 行，在 `metadata` 发出时
       * 还无从得知。所有技术栈都会发出该事件。
       */
      onOrchestrationEvent?: (eventName: string, data: unknown) => void;
      /**
       * 每个 `event: grounding` 帧触发一次——目前只有「tool」模式会发出它
       * （见 `orchestrator/modes/tool_router.py` 与
       * `orchestrator/routes/chat.py` 的流式生成器）。
       */
      onGrounding?: (report: GroundingReport) => void;
      /**
       * 对 `event: delta` 帧触发——专业智能体自身实时流式输出的文本，在
       * 「tool」模式某一轮的 `call_specialist_agent` 工具调用期间实时转发，
       * 使用户在工具调用空档期看到连续的输出流而不是一片沉默。这是一个
       * *预览*，不是最终回答：编排器随后会通过 `onChunk` 送出它自己单独
       * 组织的最终文本，内容与之重复；而后端从不会把 delta 文本持久化进
       * 保存的消息中（第 8.1 阶段）——之所以保留为独立回调而不并入
       * `onChunk`，是为了让调用方把它渲染成可替换的预览，而不是累积进
       * 最终回答也在写入的同一个缓冲区，那正是本次回调拆分所修复的可见
       * 重复问题的成因。
       */
      onDeltaChunk?: (text: string) => void;
      /**
       * 本轮对话使用的编排模式——取自 `GET /api/orchestration/modes` 的
       * `name`。省略（或为 `undefined`）时后端回退到
       * `settings.ORCHESTRATION_MODE`（默认 `"tool"`）。
       */
      mode?: string;
    } = {}
  ): Promise<{ conversation_id: string; agents_involved: string[] }> {
    const allowRefresh = options.allowRefresh ?? true;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        mode: options.mode,
      }),
      signal,
    });

    if (res.status === 401) {
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.chatStream(message, conversationId, onChunk, signal, {
            ...options,
            allowRefresh: false,
          });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("登录已过期——请重新登录。");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new Error("当前环境不支持 ReadableStream");
    }

    const decoder = new TextDecoder();
    let metadata: { conversation_id: string; agents_involved: string[] } | null = null;
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // 按双换行切分以获得完整的 SSE 事件。
        // 只按 "\n" 切分会丢失 data 载荷中内嵌的换行（例如 ```product
        // 代码块与其 JSON 正文之间的 \n），因为每一行都会被独立处理，而
        // 空的 data: 行（来自单独的 \n token）会被静默丢弃。按 SSE 规范，
        // 事件以 \n\n 结束；同一事件内的多行 data: 必须用 \n 连接，才能
        // 还原出原始值。
        const events = buffer.split("\n\n");
        // 最后一项是不完整的事件——保留在缓冲区中
        buffer = events.pop() ?? "";

        for (const event of events) {
          const lines = event.split("\n");
          let currentEventType = "";
          const dataParts: string[] = [];

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              dataParts.push(line.slice(6));
            }
          }

          if (dataParts.length === 0) continue;

          // 用 \n 重新连接多行的 data 字段（SSE 规范 §9.2.6）
          const data = dataParts.join("\n");

          if (data === "[DONE]") continue;

          if (currentEventType === "step") {
            try {
              options.onStep?.(JSON.parse(data) as AgentStep);
            } catch {
              // 忽略格式错误的 step
            }
            continue;
          }

          if (currentEventType === "metadata") {
            try {
              metadata = JSON.parse(data);
            } catch {
              // 忽略格式错误的 metadata
            }
            continue;
          }

          if (currentEventType === "grounding") {
            try {
              options.onGrounding?.(JSON.parse(data) as GroundingReport);
            } catch {
              // 忽略格式错误的事实核验报告
            }
            continue;
          }

          // ""（裸 `data:` 帧）是编排器自己的最终文本——也就是被持久化的
          // 回答。"delta"（专业智能体实时流式输出的 token）只是预览，会
          // 被路由到它自己的回调，好让调用方把它视为可替换的内容，而不是
          // 累积进与最终回答相同的缓冲区（见 onDeltaChunk 的文档）。
          if (currentEventType === "") {
            onChunk(data);
            continue;
          }
          if (currentEventType === "delta") {
            options.onDeltaChunk?.(data);
            continue;
          }

          // 其他任何具名帧（例如来自非「tool」编排模式的
          // `node`/`handoff`/`checkpoint`/`request_info`/`error`）都是结构化
          // 数据，不是展示文本——转发给可选钩子，而不是落到 onChunk，
          // 否则会把它的原始 JSON 载荷当作对话消息的一部分渲染出来。
          try {
            options.onOrchestrationEvent?.(currentEventType, JSON.parse(data));
          } catch {
            // 忽略格式错误的帧
          }
        }
      }
    } catch (err) {
      // 用户主动取消时抛出的 AbortError：不要向上抛，直接停止即可。
      if (err instanceof DOMException && err.name === "AbortError") {
        return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
      }
      throw err;
    } finally {
      reader.releaseLock();
    }

    return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
  }

  // 编排模式
  getOrchestrationModes() {
    return this.request<OrchestrationMode[]>("/api/orchestration/modes");
  }

  getModeGraph(name: string) {
    return this.request<{ name: string; mermaid: string | null }>(
      `/api/orchestration/modes/${encodeURIComponent(name)}/graph`,
    );
  }

  compareModes(message: string, modes: string[]) {
    return this.request<CompareResponse>("/api/orchestration/compare", {
      method: "POST",
      body: JSON.stringify({ message, modes }),
    });
  }

  // 会话
  getConversations() {
    return this.request<any[]>("/api/conversations");
  }

  getConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`);
  }

  deleteConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`, { method: "DELETE" });
  }

  // 智能体市场
  getAgentCatalog() {
    return this.request<any[]>("/api/marketplace/agents");
  }

  requestAccess(agentName: string, roleRequested: string, useCase: string) {
    return this.request<any>("/api/marketplace/request", {
      method: "POST",
      body: JSON.stringify({
        agent_name: agentName,
        role_requested: roleRequested,
        use_case: useCase,
      }),
    });
  }

  getMyAgents() {
    return this.request<any[]>("/api/marketplace/my-agents");
  }

  // 管理后台
  getAccessRequests() {
    return this.request<any[]>("/api/admin/requests");
  }

  approveRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  denyRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/deny`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  getAgentStats() {
    return this.request<
      { agent_name: string; request_count: number; avg_duration_ms: number; total_tokens: number }[]
    >("/api/agents/stats");
  }

  // 人工参与（HITL）
  getHitlRequests(status?: string) {
    const q = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.request<{
      requests: {
        id: string;
        user_email: string;
        agent_name: string;
        tool_name: string;
        tool_input: Record<string, unknown>;
        status: string;
        admin_note: string | null;
        approved_by: string | null;
        execution_result: Record<string, unknown> | null;
        created_at: string;
        resolved_at: string | null;
      }[];
      total: number;
    }>(`/api/admin/hitl/requests${q}`);
  }

  approveHitlRequest(id: string, note?: string) {
    return this.request<{ status: string; execution_result: Record<string, unknown> }>(
      `/api/admin/hitl/requests/${id}/approve`,
      { method: "POST", body: JSON.stringify({ note: note ?? null }) },
    );
  }

  denyHitlRequest(id: string, note?: string) {
    return this.request<{ status: string }>(
      `/api/admin/hitl/requests/${id}/deny`,
      { method: "POST", body: JSON.stringify({ note: note ?? null }) },
    );
  }

  getUsageStats() {
    return this.request<any>("/api/admin/usage");
  }

  getRuns(params?: { limit?: number; offset?: number }) {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return this.request<{
      entries: RunEntry[];
      total: number;
      limit: number;
      offset: number;
    }>(`/api/runs${qs ? `?${qs}` : ""}`);
  }

  getRunCheckpoints(runId: string) {
    return this.request<{
      run_id: string;
      checkpoints: { checkpoint_id: string; workflow_name: string; created_at: string }[];
      hitl_request: {
        id: string;
        status: "pending" | "approved" | "rejected" | "timeout";
        payload: Record<string, unknown>;
        response: Record<string, unknown> | null;
        created_at: string;
        responded_at: string | null;
      } | null;
    }>(`/api/runs/${runId}/checkpoints`);
  }

  resumeRun(runId: string, approved: boolean) {
    return this.request<{ run_id: string; approved: boolean; text: string; agents_involved: string[] }>(
      `/api/orchestration/${runId}/resume`,
      { method: "POST", body: JSON.stringify({ approved }) },
    );
  }

  getAuditLog(params?: {
    limit?: number;
    offset?: number;
    agent_name?: string;
    status?: string;
    search?: string;
  }) {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.agent_name) q.set("agent_name", params.agent_name);
    if (params?.status) q.set("status", params.status);
    if (params?.search) q.set("search", params.search);
    const qs = q.toString();
    return this.request<{
      entries: {
        id: string;
        agent_name: string;
        user_email: string | null;
        user_name: string | null;
        input_summary: string | null;
        tokens_in: number;
        tokens_out: number;
        tool_calls_count: number;
        duration_ms: number;
        status: "success" | "error";
        error_message: string | null;
        trace_id: string | null;
        created_at: string;
        steps: {
          step_index: number;
          tool_name: string;
          tool_input: Record<string, unknown> | null;
          tool_output: Record<string, unknown> | null;
          status: string;
          duration_ms: number;
        }[];
      }[];
      total: number;
      limit: number;
      offset: number;
    }>(`/api/admin/audit${qs ? `?${qs}` : ""}`);
  }

  // 商家
  getSellerProducts() {
    return this.request<{ products: any[]; total: number }>("/api/seller/products");
  }

  getSellerOrders() {
    return this.request<{ orders: any[]; total: number }>("/api/seller/orders");
  }

  getSellerStats() {
    return this.request<any>("/api/seller/stats");
  }

  // 商品
  getProducts(params?: { category?: string; min_price?: number; max_price?: number; search?: string; sort?: string }) {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.min_price !== undefined) qs.set("min_price", String(params.min_price));
    if (params?.max_price !== undefined) qs.set("max_price", String(params.max_price));
    if (params?.search) qs.set("search", params.search);
    if (params?.sort) qs.set("sort", params.sort);
    const q = qs.toString();
    return this.request<{ products: any[]; total: number; categories: string[] }>(`/api/products${q ? `?${q}` : ""}`);
  }

  getProduct(id: string) {
    return this.request<any>(`/api/products/${id}`);
  }

  // 购物车
  getCart() {
    return this.request<{
      id: string;
      items: CartItem[];
      item_count: number;
      subtotal: number;
      discount_amount: number;
      coupon_code: string | null;
      total: number;
      shipping_address: Address | null;
      billing_address: Address | null;
      billing_same_as_shipping: boolean;
    }>("/api/cart");
  }

  addToCart(productId: string, quantity: number = 1) {
    return this.request<{ status: string; product_id: string; quantity: number }>(
      "/api/cart/items",
      {
        method: "POST",
        body: JSON.stringify({ product_id: productId, quantity }),
      }
    );
  }

  updateCartItem(itemId: string, quantity: number) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "PUT",
      body: JSON.stringify({ quantity }),
    });
  }

  removeCartItem(itemId: string) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "DELETE",
    });
  }

  applyCoupon(code: string) {
    return this.request<{
      status: string;
      code: string;
      discount_amount: number;
      description: string;
    }>("/api/cart/coupon", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
  }

  removeCoupon() {
    return this.request<{ status: string }>("/api/cart/coupon", {
      method: "DELETE",
    });
  }

  updateCartAddress(data: {
    shipping_address?: Address;
    billing_address?: Address;
    billing_same_as_shipping?: boolean;
  }) {
    return this.request<{ status: string }>("/api/cart/address", {
      method: "PUT",
      body: JSON.stringify(data),
    });
  }

  checkout(data: {
    shipping_address: Address;
    billing_address?: Address | null;
    billing_same_as_shipping?: boolean;
    payment_method?: string;
  }) {
    return this.request<{
      order_id: string;
      total: number;
      item_count: number;
      status: string;
      tracking_number: string;
      carrier: string;
    }>("/api/checkout", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  cancelOrder(orderId: string, reason: string) {
    return this.request<{
      order_id: string;
      status: string;
      refund_amount: number;
    }>(`/api/orders/${orderId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  async initiateReturn(orderId: string, reason: string, refundMethod: string, operationId: string): Promise<ReturnOperation> {
    try {
      const result = await this.request<unknown>(`/api/orders/${orderId}/return`, {
        method: "POST",
        headers: { "Idempotency-Key": operationId },
        body: JSON.stringify({ reason, refund_method: refundMethod }),
      });
      return returnOperationSchema.parse(result);
    } catch (error) {
      if (error instanceof ApiError && error.data.outcome === "AWAITING_APPROVAL") {
        return returnOperationSchema.parse(error.data);
      }
      throw error;
    }
  }

  async returnOperation(operationId: string): Promise<ReturnOperation> {
    return returnOperationSchema.parse(await this.request<unknown>(`/api/returns/operations/${operationId}`));
  }

  // 订单
  getOrders(status?: string) {
    const q = status ? `?status=${status}` : "";
    return this.request<{ orders: any[]; total: number }>(`/api/orders${q}`);
  }

  getOrder(id: string) {
    return this.request<any>(`/api/orders/${id}`);
  }

  // 个人中心
  getProfile() {
    return this.request<any>("/api/profile");
  }

  getUserMemories(category?: string) {
    const q = category ? `?category=${encodeURIComponent(category)}` : "";
    return this.request<
      { id: string; category: string; content: string; importance: number; created_at: string }[]
    >(`/api/user/memories${q}`);
  }

  deleteUserMemory(id: string) {
    return this.request<{ deleted: boolean }>(`/api/user/memories/${id}`, {
      method: "DELETE",
    });
  }
}

export const api = new ApiClient();

// 便于使用的具名函数导出（委托给单例）
export function getConversations() {
  return api.getConversations();
}

export function getConversation(id: string) {
  return api.getConversation(id);
}

export function deleteConversation(id: string) {
  return api.deleteConversation(id);
}

export function chat(message: string, conversationId?: string) {
  return api.chat(message, conversationId);
}

export function chatStream(
  message: string,
  conversationId: string | undefined,
  onChunk: (text: string) => void,
) {
  return api.chatStream(message, conversationId, onChunk);
}
