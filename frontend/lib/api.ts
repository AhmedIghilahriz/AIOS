// Base API + jeton d'accès partagés par tout le frontend (un seul module = état unique).
export const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

let ACCESS_TOKEN: string | null = null;
export function setAccessToken(t: string | null) { ACCESS_TOKEN = t; }
export function authHeaders(): Record<string, string> {
  return ACCESS_TOKEN ? { Authorization: `Bearer ${ACCESS_TOKEN}` } : {};
}
