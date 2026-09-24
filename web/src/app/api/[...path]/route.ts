/**
 * 浏览器发起的每一个 `/api/*` 请求都经此同源代理转发。
 *
 * 为什么需要它：`NEXT_PUBLIC_*` 会在构建期被内联进客户端产物，而编排服务的
 * 地址在基础设施开通之前并不存在。把它写死在构建产物里，意味着 FQDN 确定后
 * 必须重新构建镜像——「一条命令完成部署」正是因此无法实现。
 *
 * 有了这个处理函数，浏览器只与自己的源通信。编排服务的地址改由
 * `ORCHESTRATOR_URL` 提供——这是一个*服务端*变量，每次请求时读取——于是同一
 * 个镜像可以对接任意后端，编排服务完全不需要公网入口，也没有 CORS 需要配置。
 *
 * 用 `next.config.ts` 里的 `rewrites()` 达不到同样效果：Next 会在
 * `next build` 阶段求值 `rewrites()` 并把结果固化进 `routes-manifest.json`，
 * 目标地址因此仍是构建期常量——只是把同一个问题换了个地方。
 */
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

/** 逐跳首部（RFC 9110 6.1），外加 undici 必须重新计算的那些。 */
const STRIP_REQUEST_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  // 参见下方 proxy() 中的 `accept-encoding: identity`。
  "accept-encoding",
]);

const STRIP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
]);

function orchestratorUrl(): string {
  return (process.env.ORCHESTRATOR_URL ?? "http://localhost:8080").replace(/\/+$/, "");
}

async function proxy(req: NextRequest): Promise<Response> {
  const target = `${orchestratorUrl()}${req.nextUrl.pathname}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  // 这里是「显式设置」而非「仅仅删除」：如果让编排服务对响应做 gzip，而 undici
  // 交回的已是解码后的正文，那么透传出去的 `content-encoding` 就在描述一个
  // 不再被编码的响应体；而 `text/event-stream` 上的 gzip 会把实时流缓冲起来。
  // 仅删除该首部不够——缺省时 undici 会替换为自己的默认值，所以必须钉住取值。
  headers.set("accept-encoding", "identity");

  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // 只要请求体是流，undici 就要求提供 `duplex`。
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
      // 在浏览器中取消一个流式对话轮次，必须同时取消上游请求，否则编排服务会
      // 继续向一个无人读取的套接字生成内容。
      signal: req.signal,
    } as RequestInit);
  } catch (err) {
    if (req.signal.aborted) {
      // 用户主动取消，不算网关故障。
      return new Response(null, { status: 499 });
    }
    console.error(`[api-proxy] ${req.method} ${req.nextUrl.pathname} -> ${target} 请求失败`, err);
    return Response.json({ detail: "无法连接编排服务。" }, { status: 502 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  // 多数反向代理默认会缓冲被代理的响应，这会把逐 token 的 SSE 流变成「最后
  // 一次性送达」——流看起来仍然「能用」，但界面不再实时。
  if (responseHeaders.get("content-type")?.includes("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
