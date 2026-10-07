"""The one page this service draws itself: sign-in, for what has no page of its own.

The React interfaces each have their own sign-in form. MLflow does not, and
is not ours to add one to, so its proxy sends anyone without a session here
(`/auth/login?next=...`) and back again afterwards. A form that posts, with
no script: it works behind any proxy, in any browser, and has nothing in it
to keep in step with the GUIs' code.
"""

from __future__ import annotations

from html import escape


def safe_next(target: str | None) -> str:
    """Where to send someone after signing in -- only ever somewhere on this site.

    A path, never a URL: `//evil.example` and `/\\evil.example` are read by
    browsers as another host, so anything but a plain absolute path is home.
    """
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def login_page(*, next_path: str, message: str = "", username: str = "") -> str:
    notice = f'<p class="problem" role="alert">{escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in -- nl2sql</title>
<style>
  :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
  body {{ display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  form {{ display: grid; gap: .75rem; width: min(22rem, 90vw); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
  label {{ display: grid; gap: .25rem; }}
  input {{ font: inherit; padding: .5rem; }}
  button {{ font: inherit; padding: .55rem; cursor: pointer; }}
  .problem {{ color: #b3261e; margin: 0; }}
</style>
</head>
<body>
<form method="post" action="login/form">
  <h1>Sign in to nl2sql</h1>
  {notice}
  <label>Name <input name="username" autocomplete="username" required autofocus value="{escape(username)}"></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <input type="hidden" name="next" value="{escape(safe_next(next_path))}">
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""
