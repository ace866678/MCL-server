import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

/**
 * Admin gate for the write operations (backup, console).
 *
 * The bridge token is a server-side secret and never reaches the browser, but
 * that alone is not enough: without a gate here, anyone who finds the Vercel
 * URL could make the server back up or run console commands. So the actions
 * require a cookie that is only issued after ADMIN_PASSWORD is supplied.
 */

const COOKIE = "mc_admin";
const MAX_AGE_SECONDS = 60 * 60 * 8;

function secret(): string | null {
  return process.env.ADMIN_PASSWORD ?? null;
}

function sign(value: string): string {
  return createHmac("sha256", secret() ?? "").update(value).digest("hex");
}

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export function adminConfigured(): boolean {
  return Boolean(secret());
}

export function checkPassword(candidate: string): boolean {
  const expected = secret();
  if (!expected) return false;
  return safeEqual(candidate, expected);
}

/** Token is `expiry.signature`, so it carries no session state to store. */
export function issueToken(): string {
  const expiry = Date.now() + MAX_AGE_SECONDS * 1000;
  return `${expiry}.${sign(String(expiry))}`;
}

export function tokenValid(token: string | undefined): boolean {
  const expected = secret();
  if (!expected || !token) return false;
  const [expiry, signature] = token.split(".");
  if (!expiry || !signature) return false;
  if (!safeEqual(signature, sign(expiry))) return false;
  return Number(expiry) > Date.now();
}

export async function isAdmin(): Promise<boolean> {
  const store = await cookies();
  return tokenValid(store.get(COOKIE)?.value);
}

export async function setSessionCookie(): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, issueToken(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  store.delete(COOKIE);
}