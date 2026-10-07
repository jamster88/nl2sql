/**
 * Wording helpers: the ones every page shares (`web/src/text.ts`). A
 * submission's narrative is a snapshot taken at vote time, and one staged
 * before agent 5.1.1 reads `Thrift &amp; Table` -- `plainText` shows it as
 * `Thrift & Table`; on plain text it changes nothing. `counted` agrees a
 * count with its noun.
 */
export { counted, plainText } from "@nl2sql/web/text";
