"""The acceptance tier (V6-67): the whole stack, started the way a user starts
it, used the way a user uses it.

Opt-in (`pytest --run-acceptance`), and slow -- ten to twenty minutes, most of
it the first build. Every other tier tests a part: units at 100% in every
language, a live test per service, compose resolved, the scripts against a
fake `docker`. 6.0 passed all of them and shipped four defects that only the
running stack could show -- the directory restarting for ever on a volume
another container had created, `launch.sh` leaving the database unprepared
for sign-in, five wrong passwords from one address locking everyone out, and
the desktop client calling a server that asked who it was "not connected".
Each has a test below that would have failed.

What runs is a copy of this checkout, so its `.env`, its golden question
document and its desktop jar are its own, as a stack of its own beside any
other on the machine: `NL2SQL_INSTANCE` names its containers, its volumes and
its compose project, and every port is a free one. Then the user's two
commands, with nothing in between:

    ./setup.sh --build-all --review --curate --console --mlflow --desktop
    ./start.sh --review --curate --console --mlflow --no-browser

`--build-all` builds every image from the checkout rather than pulling the
published ones, which is the point: this is the test a release passes before
it is published. `./launch.sh --desktop` then builds the desktop client's jar,
which the last test drives.

The tests sign in as the administrator the directory made on its first
start, with the password `setup.sh` generated -- read from the copy's `secrets/`
and never printed -- and then: every page, a question through the web
interface, its verdict and its promotion into the golden set, the SQL
console, MLflow behind its front door with the question's trace in it, and
the desktop client's own classes. Everything is torn down afterwards, volumes
included, unless NL2SQL_ACCEPTANCE_KEEP is set.

A question needs a chat model. The tier asks the same one this checkout's
`.env` names (OLLAMA_BASE_URL, OLLAMA_MODEL, EMBED_BASE_URL, in the
environment or in `.env`); when the API says it cannot reach it, the tests
that need an answer are skipped, saying so, and the rest still run.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
#: Every page, and so every profile a user's start brings up.
PAGES = ("--review", "--curate", "--console", "--mlflow")
#: What a second stack needs beside a running one, measured: the services
#: settle near 2.7 GiB, and MLflow's server alone briefly takes 1.8 on its
#: first start. Below this the machine's other stack is what pays, in
#: containers the kernel kills to make room -- which happened once.
HEADROOM_MIB = 3584
#: The ports a stack publishes, each moved to a free one for this run.
PORTS = (
    "POSTGRES_PORT", "CONTEXT_DB_PORT", "VECTOR_DB_PORT", "STORES_DB_PORT", "API_PORT", "REVIEW_PORT", "CONSOLE_PORT",
    "AUTH_PORT", "GUI_PORT", "REVIEW_GUI_PORT", "CURATE_GUI_PORT", "CONSOLE_GUI_PORT",
    "DIRECTORY_GUI_PORT", "MLFLOW_PORT",
)
#: The model settings carried from this checkout's own `.env`, none of them
#: secret: which host answers, with which models.
MODEL_SETTINGS = ("OLLAMA_BASE_URL", "OLLAMA_MODEL", "EMBED_BASE_URL", "EMBED_MODEL")
QUESTION = "How many stores are there?"


def _dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip()
    return values


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _docker(*args: str, check: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def _mib(text: str) -> float:
    number = float(re.match(r"[\d.]+", text).group(0))
    unit = text[len(re.match(r"[\d.]+", text).group(0)):]
    return number * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024, "B": 1 / 1048576}.get(unit, 1)


def _headroom() -> float:
    """Memory Docker's machine has left, in MiB: its total less what every
    running container holds now."""
    total = int(_docker("info", "--format", "{{.MemTotal}}", check=True).stdout.strip()) / 1048576
    used = _docker("stats", "--no-stream", "--format", "{{.MemUsage}}", check=True).stdout.split("\n")
    return total - sum(_mib(line.split(" / ")[0]) for line in used if line.strip())


def _copy_checkout(into: Path) -> None:
    """Every file git knows of or would not ignore, as it is in the working
    tree now -- uncommitted changes included, `.env` and builds not."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    for name in filter(None, listed):
        source = REPO_ROOT / name
        if source.is_file():
            target = into / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Bind-mounted, and ignored by git: made here rather than by Docker,
    # which would make them root's.
    for directory in ("ldap/seed", "desktop/target"):
        (into / directory).mkdir(parents=True, exist_ok=True)


@dataclass
class Stack:
    root: Path
    instance: str
    ports: dict[str, int]
    log: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def ca(self) -> Path:
        return self.root / "nl2sql-ca.crt"

    @property
    def admin_password(self) -> str:
        """As setup.sh generated it: a file in secrets/ since 6.3 (V6-38)."""
        return (self.root / "secrets" / "ldap_admin_password").read_text().strip()

    def url(self, port: str, path: str = "") -> str:
        return f"https://localhost:{self.ports[port]}{path}"

    def run(self, *command: str, timeout: int) -> subprocess.CompletedProcess:
        with self.log.open("a") as log:
            log.write(f"\n\n$ {' '.join(command)}\n")
            log.flush()
            return subprocess.run(
                list(command), cwd=self.root, env=self.env, stdout=log, stderr=subprocess.STDOUT,
                text=True, timeout=timeout,
            )

    def compose(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        profiles = [flag for name in ("api", "gui", "review", "reviewgui", "curategui", "console",
                                      "consolegui", "auth", "directorygui", "mlflow", "desktop", "migrate")
                    for flag in ("--profile", name)]
        return subprocess.run(["docker", "compose", *profiles, *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def tail(self, lines: int = 60) -> str:
        return "\n".join(self.log.read_text().splitlines()[-lines:])


@pytest.fixture(scope="module")
def stack(tmp_path_factory) -> Stack:
    if shutil.which("docker") is None or _docker("info").returncode != 0:
        pytest.fail("the acceptance tier needs a working Docker daemon")
    free = _headroom()
    if free < HEADROOM_MIB:
        pytest.fail(
            f"Docker's machine has {free:.0f} MiB free and a second stack needs about {HEADROOM_MIB}: "
            "stop another stack first, or give Docker more memory"
        )
    root = tmp_path_factory.mktemp("acceptance") / "nl2sql"
    _copy_checkout(root)
    instance = f"nl2sql-accept-{uuid.uuid4().hex[:6]}"
    ports = {name: _free_port() for name in PORTS}
    ours = _dotenv(REPO_ROOT / ".env")
    model = {key: os.environ.get(key) or ours.get(key, "") for key in MODEL_SETTINGS}
    # The seed: what makes this stack another one. setup.sh keeps every key
    # it does not write itself, so these outlive its rewriting the file.
    seed = [f"NL2SQL_INSTANCE={instance}", *(f"{key}={value}" for key, value in ports.items())]
    seed.append(f"MLFLOW_PROXY_PORT={ports['MLFLOW_PORT']}")
    (root / ".env").write_text("\n".join(seed) + "\n")
    # The scripts read the shell before .env, as compose does, so a variable
    # a developer exported for their own stack would steer this one.
    keep = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SHELL", "DOCKER_HOST", "DOCKER_CONTEXT")
    environment = {key: os.environ[key] for key in keep if key in os.environ}
    environment["TERM"] = "dumb"
    built = Stack(root=root, instance=instance, ports=ports, log=root / "acceptance.log", env=environment)

    setup = ["./setup.sh", "--build-all", *PAGES, "--desktop"]
    for flag, key in (("--ollama-url", "OLLAMA_BASE_URL"), ("--model", "OLLAMA_MODEL"),
                      ("--embed-url", "EMBED_BASE_URL"), ("--embed-model", "EMBED_MODEL")):
        if model[key]:
            setup += [flag, model[key]]
    try:
        for command, minutes in ((setup, 45), (["./start.sh", *PAGES, "--no-browser"], 30),
                                 (["./launch.sh", "--desktop"], 20)):
            done = built.run(*command, timeout=minutes * 60)
            assert done.returncode == 0, f"{' '.join(command)} failed:\n{built.tail()}"
        yield built
    finally:
        if not os.environ.get("NL2SQL_ACCEPTANCE_KEEP"):
            built.compose("down", "--volumes", "--remove-orphans")
            _docker("volume", "rm", f"{instance}-pgdata")


# ---------------------------------------------------------------------------
# Talking to it as a browser does
# ---------------------------------------------------------------------------


@dataclass
class Answer:
    status: int
    body: object
    headers: dict[str, str]


def _call(stack: Stack, method: str, url: str, body=None, *, cookie: str = "", basic: str = "",
          origin: str = "", timeout: int = 60) -> Answer:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    if origin:
        headers.update({"Origin": origin, "Sec-Fetch-Site": "same-origin"})
    request = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    context = ssl.create_default_context(cafile=str(stack.ca))
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            status, raw, got = response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as error:
        status, raw, got = error.code, error.read().decode(), dict(error.headers)
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw
    return Answer(status, parsed, {key.lower(): value for key, value in got.items()})


def _sign_in(stack: Stack, page: str, username: str = "admin", password: str | None = None) -> Answer:
    origin = stack.url(page)
    return _call(stack, "POST", f"{origin}/auth/login",
                 {"username": username, "password": password or stack.admin_password}, origin=origin)


@pytest.fixture(scope="module")
def sessions(stack: Stack) -> dict[str, str]:
    """The administrator, signed in on every page, as a browser keeps it."""
    found = {}
    for page in ("GUI_PORT", "REVIEW_GUI_PORT", "CURATE_GUI_PORT", "CONSOLE_GUI_PORT", "DIRECTORY_GUI_PORT"):
        answer = _sign_in(stack, page)
        assert answer.status == 200, f"signing in on {page}: {answer.status} {answer.body}"
        found[page] = answer.headers["set-cookie"].split(";", 1)[0]
    return found


# ---------------------------------------------------------------------------
# The four defects 6.0 shipped
# ---------------------------------------------------------------------------


def test_every_container_is_up_healthy_and_has_never_restarted(stack: Stack):
    """6.0: the directory, started on a volume the auth service's container
    had been created on, could not write its certificate and restarted for
    ever. Compose creating the containers in its own order is what did it,
    so only a stack compose made can show it."""
    rows = _docker("ps", "--all", "--filter", f"label=com.docker.compose.project={stack.instance}",
                   "--format", "{{.Names}}", check=True).stdout.split()
    # 19 since 6.3: the four runtime stores are one server, and the dbprep
    # service prepares the databases as the pki service issues certificates.
    assert len(rows) >= 19, rows
    for name in rows:
        state = json.loads(_docker("inspect", "--format", "{{json .State}}", name, check=True).stdout)
        restarts = int(_docker("inspect", "--format", "{{.RestartCount}}", name, check=True).stdout)
        if name.endswith(("-pki", "-dbprep")):
            assert (state["Status"], state["ExitCode"]) == ("exited", 0), name
            continue
        assert state["Status"] == "running" and restarts == 0, f"{name}: {state['Status']}, {restarts} restarts"
        if state.get("Health"):
            assert state["Health"]["Status"] == "healthy", name


def test_nothing_runs_as_root_writes_its_image_or_shows_a_secret(stack: Stack):
    """6.3 (V6-34, V6-38), held across the whole stack as compose made it:
    no process in any container is root's -- those that start as root to hand
    over their volumes have dropped by now -- every root filesystem is
    read-only and every container has given up new privileges, and no
    password or token is in any container's environment or command line,
    where `docker inspect` would show it."""
    secrets = [path.read_text().strip() for path in (stack.root / "secrets").iterdir()]
    assert len([value for value in secrets if value]) >= 15
    rows = _docker("ps", "--filter", f"label=com.docker.compose.project={stack.instance}",
                   "--format", "{{.Names}}", check=True).stdout.split()
    for name in rows:
        # With the PID: Docker's `top` refuses a format without one. The user
        # is named as Docker's own machine names that uid -- nginx's 101 may
        # read `statd` -- so root is the one name that matters.
        processes = _docker("top", name, "-o", "pid,user,args", check=True).stdout.splitlines()[1:]
        assert not [line for line in processes if line.split()[1] in ("root", "0")], f"{name}: {processes}"
        host = json.loads(_docker("inspect", "--format", "{{json .HostConfig}}", name, check=True).stdout)
        assert host["ReadonlyRootfs"] and "no-new-privileges:true" in host["SecurityOpt"], name
        assert "ALL" in host["CapDrop"] and host["Memory"] > 0 and host["PidsLimit"] > 0, name
    for name in rows + [f"{stack.instance}-dbprep", f"{stack.instance}-pki"]:
        shown = _docker("inspect", "--format", "{{json .Config.Env}} {{json .Config.Cmd}} {{json .Args}}", name).stdout
        assert not [value for value in secrets if value and value in shown], f"a secret is in {name}'s configuration"


def test_the_generated_administrator_signs_in_on_every_page_and_each_reaches_its_service(stack, sessions):
    """6.0: `launch.sh` did not pass the directory's host to the script that
    prepares the database for sign-in, and nobody could sign in."""
    for page, path in (("GUI_PORT", "/v1/meta"), ("REVIEW_GUI_PORT", "/v1/meta"),
                       ("CURATE_GUI_PORT", "/v1/meta"), ("CONSOLE_GUI_PORT", "/v1/meta"),
                       ("DIRECTORY_GUI_PORT", "/directory/v1/people")):
        answer = _call(stack, "GET", stack.url(page, path), cookie=sessions[page])
        assert answer.status == 200, f"{page}{path}: {answer.status} {answer.body}"
    assert _call(stack, "GET", stack.url("GUI_PORT", "/v1/meta")).status == 401


def test_wrong_passwords_from_one_address_do_not_lock_out_the_next_person(stack: Stack):
    """6.0: five wrong passwords per address -- and every browser on a
    machine, behind nginx and Docker's NAT, is one address -- locked
    everybody out of signing in for fifteen minutes."""
    stranger = f"nobody-{uuid.uuid4().hex[:6]}"
    refused = [_sign_in(stack, "GUI_PORT", stranger, "wrong").status for _ in range(6)]
    assert set(refused) <= {401, 429} and refused[0] == 401, refused
    assert _sign_in(stack, "GUI_PORT").status == 200, "one person's typing locked out the administrator"


def test_the_desktop_clients_own_classes_sign_in_when_the_server_asks(stack: Stack, tmp_path: Path):
    """6.0: `/v1/meta` became a route that answers only someone signed in,
    the client's first call got a 401, and the window said "Not connected."
    -- its tests' fake server had answered anyone. Here are the client's own
    HTTP and sign-in classes, from the jar `launch.sh --desktop` built,
    against the real server: the 401 the window turns into its sign-in
    panel, then the same question answered once signed in."""
    java = shutil.which("java")
    if java is None:
        pytest.skip("no java on PATH to run the desktop client's classes with")
    jar = stack.root / "desktop" / "target" / "nl2sql-desktop.jar"
    assert jar.is_file(), "launch.sh --desktop built no jar"
    probe = tmp_path / "Probe.java"
    probe.write_text(
        "import org.nl2sql.desktop.Settings;\n"
        "import org.nl2sql.desktop.api.*;\n"
        "public class Probe {\n"
        "    public static void main(String[] args) {\n"
        "        Settings settings = Settings.from(System.getenv(), args);\n"
        "        Session session = new Session(settings.token());\n"
        "        HttpApiClient client = new HttpApiClient(settings, session);\n"
        "        try {\n"
        "            client.meta();\n"
        "            System.out.println(\"before: answered\");\n"
        "        } catch (ApiException asked) {\n"
        "            System.out.println(\"before: \" + asked.status());\n"
        "        }\n"
        "        new HttpSignIn(settings, session).signIn(\"admin\", System.getenv(\"PROBE_PASSWORD\"));\n"
        "        System.out.println(\"after: \" + client.meta().version());\n"
        "    }\n"
        "}\n"
    )
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""),
                   "PROBE_PASSWORD": stack.admin_password}
    done = subprocess.run(
        # What `start.sh --desktop` passes the window.
        [java, "-cp", str(jar), str(probe), "--url", stack.url("API_PORT"), "--auth-url", stack.url("AUTH_PORT"),
         "--cacert", str(stack.ca)],
        capture_output=True, text=True, timeout=180, env=environment,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    from nl2sql_agent import __version__

    assert done.stdout.split() == ["before:", "401", "after:", __version__], done.stdout


# ---------------------------------------------------------------------------
# A user's afternoon
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def answered(stack: Stack, sessions) -> dict:
    """A question through the web interface, as its page asks it."""
    ready = _call(stack, "GET", stack.url("API_PORT", "/readyz"), timeout=60)
    agent = (ready.body or {}).get("checks", {}).get("agent", {}) if isinstance(ready.body, dict) else {}
    if not agent.get("ok"):
        pytest.skip(f"the API cannot reach its chat model: {agent.get('detail', ready.body)}")
    page, cookie = stack.url("GUI_PORT"), sessions["GUI_PORT"]
    asked = _call(stack, "POST", f"{page}/v1/questions?wait=1", {"question": QUESTION},
                  cookie=cookie, origin=page)
    assert asked.status in (200, 202), asked.body
    job = asked.body
    deadline = time.monotonic() + 600
    while job["status"] not in ("succeeded", "failed", "cancelled") and time.monotonic() < deadline:
        job = _call(stack, "GET", f"{page}/v1/questions/{job['id']}?wait=20", cookie=cookie, timeout=90).body
    assert job["status"] == "succeeded", job.get("error") or job["status"]
    return job


def test_a_question_through_the_web_interface_is_answered_with_its_working(answered: dict):
    answer = answered["answer"]
    assert "dim_store" in answer["sql"].lower(), answer["sql"]
    assert answer["result"]["row_count"] >= 1 and answer["narrative"]
    assert answer["node_errors"] == {}, answer["node_errors"]
    assert any(entry["model"] for entry in answer["trace"]), "no model call in the trace"


def test_its_verdict_reaches_review_and_is_promoted_into_the_golden_set(stack, sessions, answered):
    gui, review = stack.url("GUI_PORT"), stack.url("REVIEW_GUI_PORT")
    said = _call(stack, "POST", f"{gui}/v1/questions/{answered['id']}/feedback", {"verdict": "yes"},
                 cookie=sessions["GUI_PORT"], origin=gui)
    assert said.status in (200, 201), said.body
    queue = _call(stack, "GET", f"{review}/v1/submissions?state=pending", cookie=sessions["REVIEW_GUI_PORT"])
    [submission] = [s for s in queue.body["submissions"] if s["job_id"] == answered["id"]]
    draft = {
        "title": "Acceptance: how many stores", "question": submission["question"],
        "tables": submission["tables"] or "dim_store", "keywords": "count, stores",
        "reasoning_target": "Counting the rows of the store dimension",
        "sql_code": submission["sql_code"], "result": "One row: the number of stores",
    }
    promoted = _call(stack, "POST", f"{review}/v1/submissions/{submission['id']}/promote", {"draft": draft},
                     cookie=sessions["REVIEW_GUI_PORT"], origin=review, timeout=180)
    assert promoted.status == 200, promoted.body
    pair = promoted.body["pair_id"]
    document = (stack.root / "context_questions" / "translated_questions.md").read_text()
    assert f"## {pair} " in document and QUESTION in document


def test_the_console_runs_sql_as_the_person_signed_in(stack, sessions):
    page = stack.url("CONSOLE_GUI_PORT")
    ran = _call(stack, "POST", f"{page}/v1/query", {"sql": "SELECT current_user, count(*) FROM dim_store",
                                                   "mode": "run"},
                cookie=sessions["CONSOLE_GUI_PORT"], origin=page)
    assert ran.status == 200, ran.body
    assert ran.body["rows"][0][0] == "admin", ran.body["rows"]


def test_mlflow_answers_only_through_its_front_door_and_holds_the_questions_trace(stack, answered):
    door = stack.url("MLFLOW_PORT")
    search = f"{door}/api/2.0/mlflow/experiments/search?max_results=10"
    assert _call(stack, "GET", search).status == 401
    found = _call(stack, "GET", search, basic=f"admin:{stack.admin_password}")
    assert found.status == 200, found.body
    [experiment] = [e for e in found.body["experiments"] if e["name"] == "nl2sql-agent"]
    deadline, traces = time.monotonic() + 120, []
    while time.monotonic() < deadline:
        listed = _call(stack, "GET", f"{door}/api/2.0/mlflow/traces?experiment_ids={experiment['experiment_id']}"
                       f"&max_results=50", basic=f"admin:{stack.admin_password}")
        traces = (listed.body or {}).get("traces", []) if isinstance(listed.body, dict) else []
        if any(QUESTION in json.dumps(trace) for trace in traces):
            break
        time.sleep(5)
    assert any(QUESTION in json.dumps(trace) for trace in traces), f"{len(traces)} traces, none of the question"


def test_the_directory_page_lists_the_administrator(stack, sessions):
    people = _call(stack, "GET", stack.url("DIRECTORY_GUI_PORT", "/directory/v1/people"),
                   cookie=sessions["DIRECTORY_GUI_PORT"])
    assert "admin" in {person["uid"] for person in people.body["people"]}
