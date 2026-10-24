"use client";

/** 统一 API 客户端（浏览器端，XMLHttpRequest 实现）。
 *  安全约束：仅允许访问来源白名单 BASE 下的 /api/ 相对路径，路径与来源双重校验。 */

const ALLOWED_BASES = ["http://127.0.0.1:8000", "http://localhost:8000"] as const;
const BASE: string = ALLOWED_BASES.includes(
  (process.env.NEXT_PUBLIC_API_BASE || "") as (typeof ALLOWED_BASES)[number],
)
  ? (process.env.NEXT_PUBLIC_API_BASE as string)
  : ALLOWED_BASES[0];

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("vaclead_token");
}

export function setAuth(token: string, role: string, displayName: string) {
  localStorage.setItem("vaclead_token", token);
  localStorage.setItem("vaclead_role", role);
  localStorage.setItem("vaclead_user", displayName);
}

export function logout() {
  localStorage.removeItem("vaclead_token");
  localStorage.removeItem("vaclead_role");
  localStorage.removeItem("vaclead_user");
  window.location.href = "/login";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** 发起受约束的 XHR：路径必须以 /api/ 开头，解析后的 origin 必须在白名单内。 */
function xhr(
  method: string,
  path: string,
  body?: unknown,
  isForm = false,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!path.startsWith("/api/")) {
      reject(new ApiError(400, "非法请求路径"));
      return;
    }
    const parsed = new URL(path, BASE);
    if (!ALLOWED_BASES.includes(parsed.origin as (typeof ALLOWED_BASES)[number])) {
      reject(new ApiError(400, "非法请求来源"));
      return;
    }
    const req = new XMLHttpRequest();
    req.open(method, parsed.toString(), true);
    req.timeout = 30000;
    if (!isForm) req.setRequestHeader("Content-Type", "application/json");
    const token = getToken();
    if (token) req.setRequestHeader("Authorization", `Bearer ${token}`);
    req.onload = () => {
      if (req.status === 401 && !path.startsWith("/api/auth")) {
        logout();
        reject(new ApiError(401, "登录已过期"));
        return;
      }
      if (req.status >= 200 && req.status < 300) {
        try {
          resolve(JSON.parse(req.responseText || "{}"));
        } catch {
          resolve({});
        }
      } else {
        let detail = `HTTP ${req.status}`;
        try {
          const parsedBody = JSON.parse(req.responseText);
          detail =
            typeof parsedBody.detail === "string"
              ? parsedBody.detail
              : JSON.stringify(parsedBody.detail ?? parsedBody);
        } catch {
          /* ignore */
        }
        reject(new ApiError(req.status, detail));
      }
    };
    req.onerror = () => reject(new ApiError(0, "网络错误"));
    req.ontimeout = () => reject(new ApiError(0, "请求超时"));
    if (body === undefined) {
      req.send();
    } else if (isForm) {
      req.send(body as FormData);
    } else {
      req.send(JSON.stringify(body));
    }
  });
}

export const api = {
  get: <T>(path: string) => xhr("GET", path) as Promise<T>,
  post: <T>(path: string, body?: unknown) =>
    xhr("POST", path, body, body instanceof FormData) as Promise<T>,
  patch: <T>(path: string, body: unknown) => xhr("PATCH", path, body) as Promise<T>,
  put: <T>(path: string, body: unknown) => xhr("PUT", path, body) as Promise<T>,
};

export interface PageData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Enterprise {
  id: number;
  name: string;
  province: string | null;
  city: string | null;
  district: string | null;
  address: string | null;
  industry_key: string | null;
  industry_name: string | null;
  business_scope: string | null;
  registered_capital_wan: number | null;
  icp_score: number;
  icp_reasons: { rule: string; points: number }[];
  intent_score: number;
  is_competitor: number;
  signal_count?: number;
  signals?: SignalItem[];
}

export interface SignalItem {
  id: number;
  type: string;
  title: string;
  url: string;
  pub_date: string | null;
  matched_terms: string[];
  hit_part: string;
  raw_excerpt: string;
  base_score: number;
}

export interface Lead {
  id: number;
  enterprise_id: number;
  status: string;
  owner_id: number | null;
  grade: string | null;
  lost_reason: string | null;
  last_followed_at: string | null;
  enterprise?: { id: number; name: string; industry_name: string | null; icp_score: number; intent_score: number };
}

export interface FollowupItem {
  id: number;
  content: string;
  next_action: string | null;
  next_time: string | null;
  created_at: string;
}
