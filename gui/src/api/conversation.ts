/**
 * The page's conversation (8.0).
 *
 * The server keeps a conversation by an id its client chooses, so the page
 * makes one when it opens and another when "New conversation" is pressed,
 * and sends it with every question while `/v1/meta` says the server
 * remembers anything. Random, so two pages -- or two people, with sign-in
 * off -- never share one: a version 4 UUID is 122 random bits.
 */

import type { Meta } from "./types";

/**
 * A new conversation's id.
 *
 * `crypto.randomUUID` exists only where the page is a secure context, HTTPS
 * or localhost. The proxy serves the page over HTTPS, but a deployment that
 * serves it over plain HTTP from another host still has
 * `crypto.getRandomValues`, so the same 122 bits are made from that.
 */
export function newConversationId(source: Pick<Crypto, "getRandomValues"> & Partial<Pick<Crypto, "randomUUID">> = globalThis.crypto): string {
  if (typeof source.randomUUID === "function") return source.randomUUID();
  // The version (4) in byte 6 and the variant (10) in byte 8, as RFC 9562
  // sets them; the other 122 bits are the source's.
  const bytes = Array.from(source.getRandomValues(new Uint8Array(16)), (byte, index) =>
    index === 6 ? (byte & 0x0f) | 0x40 : index === 8 ? (byte & 0x3f) | 0x80 : byte,
  );
  const hex = bytes.map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

/**
 * How many answered turns this server reads a question with: 0 when it keeps
 * none (`CONVERSATION_MEMORY=0`), when it is older than 8.0, and before
 * `/v1/meta` has answered.
 */
export function remembers(meta: Meta | null): number {
  return meta?.pipeline.memory?.turns ?? 0;
}

/** The status bar's sentence about it. */
export function memorySentence(meta: Meta): string {
  const turns = remembers(meta);
  if (turns === 0) return "each question asked alone";
  return turns === 1 ? "remembers the last turn" : `remembers the last ${turns} turns`;
}
