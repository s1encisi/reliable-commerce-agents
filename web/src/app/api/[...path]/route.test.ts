import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import { GET, POST } from "./route";

/**
 * `/api/*` 代理是前端通往编排服务的唯一传输路径，因此这里锁定的正是那些
 * 一旦出错就会「静默失效」的行为：转发到错误的 URL、丢掉 `Authorization`、
 * 透传一个已不再描述响应体的 `content-encoding`，以及把 SSE 流缓冲起来，
 * 导致对话回答在最后才一次性送达。
 */

function makeRequest(init: {
  method?: string;
  pathname?: string;
  search?: string;
  headers?: Record<string, string>;
  body?: string | null;
  aborted?: boolean;
}): NextRequest {
  const controller = new AbortController();
  if (init.aborted) controller.abort();
  return {
    method: init.method ?? "GET",
    headers: new Headers(init.headers ?? {}),
    nextUrl: { pathname: init.pathname ?? "/api/orders", search: init.search ?? "" },
    body: init.body ?? null,
    signal: controller.signal,
  } as unknown as NextRequest;
}

/**
 * 按代理实际调用 `fetch` 的签名来打桩。若不加这个类型断言，
 * `vi.fn(async () => ...)` 会被推断为零参数函数，`mock.calls[i]` 随之退化为
 * 空元组，下面所有针对实参的断言都将无法通过类型检查。
 */
function stubFetch(impl: () => Promise<Response>) {
  const spy = vi.fn(impl as unknown as (url: string, init: RequestInit) => Promise<Response>);
  vi.stubGlobal("fetch", spy);
  return spy;
}

let originalOrchestratorUrl: string | undefined;

beforeEach(() => {
  originalOrchestratorUrl = process.env.ORCHESTRATOR_URL;
  process.env.ORCHESTRATOR_URL = "http://orchestrator:8080";
});

afterEach(() => {
  if (originalOrchestratorUrl === undefined) delete process.env.ORCHESTRATOR_URL;
  else process.env.ORCHESTRATOR_URL = originalOrchestratorUrl;
  vi.unstubAllGlobals();
});

describe("api 代理", () => {
  it("把方法、路径与查询串转发到 ORCHESTRATOR_URL", async () => {
    const fetchSpy = stubFetch(async () => new Response("[]", { status: 200 }));

    await GET(makeRequest({ pathname: "/api/orders", search: "?limit=50" }));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("http://orchestrator:8080/api/orders?limit=50");
    expect(fetchSpy.mock.calls[0][1].method).toBe("GET");
  });

  it("容忍 ORCHESTRATOR_URL 末尾带斜杠", async () => {
    process.env.ORCHESTRATOR_URL = "http://orchestrator:8080/";
    const fetchSpy = stubFetch(async () => new Response("[]", { status: 200 }));

    await GET(makeRequest({ pathname: "/api/orders" }));

    expect(fetchSpy.mock.calls[0][0]).toBe("http://orchestrator:8080/api/orders");
  });

  it("保留 Authorization、丢弃逐跳首部、钉住 accept-encoding", async () => {
    const fetchSpy = stubFetch(async () => new Response("{}", { status: 200 }));

    await GET(
      makeRequest({
        headers: {
          authorization: "Bearer token-123",
          "accept-encoding": "gzip, br",
          connection: "keep-alive",
          host: "localhost:3000",
        },
      }),
    );

    const sent = fetchSpy.mock.calls[0][1].headers as Headers;
    expect(sent.get("authorization")).toBe("Bearer token-123");
    // 钉为 identity 而非删除：首部缺失时 undici 会替换为自己的默认值，所以
    // 直接删掉并不能真正阻止编排服务压缩响应——包括 SSE 流。
    expect(sent.get("accept-encoding")).toBe("identity");
    expect(sent.get("connection")).toBeNull();
    expect(sent.get("host")).toBeNull();
  });

  it("POST 转发请求体，GET 不转发", async () => {
    const fetchSpy = stubFetch(async () => new Response("{}", { status: 200 }));

    await POST(makeRequest({ method: "POST", pathname: "/api/chat/stream", body: "{}" }));
    expect(fetchSpy.mock.calls[0][1].body).toBe("{}");

    await GET(makeRequest({ method: "GET" }));
    expect(fetchSpy.mock.calls[1][1].body).toBeUndefined();
  });

  it("把 SSE 响应标记为不缓冲，保证对话流实时", async () => {
    stubFetch(
      async () =>
        new Response("data: hi\n\n", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    );

    const res = await POST(makeRequest({ method: "POST", pathname: "/api/chat/stream", body: "{}" }));

    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(res.headers.get("cache-control")).toBe("no-cache, no-transform");
  });

  it("剥离已不再描述响应体的响应首部", async () => {
    stubFetch(
      async () =>
        new Response("{}", {
          status: 200,
          headers: {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "x-request-id": "abc",
          },
        }),
    );

    const res = await GET(makeRequest({}));

    expect(res.headers.get("content-encoding")).toBeNull();
    expect(res.headers.get("content-type")).toBe("application/json");
    // 编排服务设置的其余首部必须原样穿过这一跳。
    expect(res.headers.get("x-request-id")).toBe("abc");
  });

  it("保留上游状态码，不做归一化", async () => {
    stubFetch(async () => new Response("{}", { status: 401 }));

    const res = await GET(makeRequest({}));

    // api.ts 的整个「刷新并重放」流程都由 401 驱动，代理若把它变成 502，
    // 反而会把用户登出。
    expect(res.status).toBe(401);
  });

  it("编排服务不可达时返回 502", async () => {
    stubFetch(async () => {
      throw new Error("ECONNREFUSED");
    });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const res = await GET(makeRequest({}));

    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({ detail: "无法连接编排服务。" });
  });

  it("不把被取消的流当作网关故障", async () => {
    stubFetch(async () => {
      throw new DOMException("The operation was aborted.", "AbortError");
    });

    const res = await GET(makeRequest({ aborted: true }));

    expect(res.status).toBe(499);
  });
});
