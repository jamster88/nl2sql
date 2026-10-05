# The shared web package

`@nl2sql/web` (6.2.0): what every page of the stack shares -- the web
interface, the review and curation interfaces, the SQL console and the
directory page. Until 6.2 each carried its own copy (V6-25): five identical
sign-in gates, five session clients, five tests of them, and three slightly
different error classes.

| File | What |
| --- | --- |
| `src/auth/session.ts` | The sign-in client: who is signed in, signing in and out, changing a password, and the event a 401 raises so the gate asks again |
| `src/auth/SignInGate.tsx` | The gate every page renders inside: the sign-in form, the signed-in bar, the password dialog |
| `src/api/errors.ts` | `ApiError`, what every page's client throws for the stack's one error envelope |
| `src/text.ts` | `plainText` (the agent's three entities, undone -- the desktop's `Markup.plain` is held to the same rule) and `counted` |
| `src/vite.ts` | `devProxies`: the dev server's proxies, built the same way by every page |
| `src/config.ts` | `sharedPackage`: how a page's Vite and Vitest configuration take this package in |

## How a page takes it in

It is source, not a build, and it has no dependencies of its own. A page
imports `@nl2sql/web/...`, which its configuration resolves here
(`sharedPackage(import.meta.url)`), and compiles it with its own toolchain;
React and the testing library are the page's, resolved from the page's
`node_modules` (`dedupe`), so a page never holds two Reacts. `tsconfig.json`
says the same to TypeScript. Each page's image copies `web/package.json` and
`web/src/` to `/web`, which is `../web` (or `../../web`) from its `/build`.

## Tests

`test/` runs in every page's suite, against that page's React, and this
package's sources count towards every page's 100% coverage. From the
repository's side, `tests/web/test_web_package.py` holds every page to taking
it in the same way, and the desktop to the same unescaping rule.
