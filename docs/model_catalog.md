# Model catalog

*Part of the [nl2sql documentation](../README.md#documentation).*

[`models/`](../models) holds the list arch5.2's model router routes from: every
model the chat host serves, what the host and ollama.com say about it, and
the highest rung of each task -- light, standard or heavy -- it is suited to.

```bash
python3 models/build_catalog.py                  # the host .env points at -> models/catalog.json
python3 models/build_catalog.py 192.168.1.20     # any Ollama host, by its address alone
.venv/bin/python models/calibrate.py             # measure what each model is suited to
```

The scanner needs only the standard library, so it runs with the `python3` a
Mac already has; it says what each model *is* and guesses from that what it
is suited to. The calibrator runs each model through a probe per task
against the live stack and records what it *measured*, and only a measured
suitability is routed on. A full calibration of a host with dozens of models
takes hours, so it measures each set of identical builds once, stops probing
a generator that cannot be suited, survives any one model failing, and
resumes where it stopped. A catalog describes one host: the committed one is
ignored anywhere else, so build and calibrate your own. Re-run the scanner
when the host's models change -- it keeps the measurements of every model
whose weights have not -- and commit the result like code.
[`models/README.md`](../models/README.md) has the rules, the probes and the
catalog's shape.

`launch.sh`, and so `start.sh`, asks the agent image on every start which
models it will route to, and says: how many models the calls are shared
between, or why every one goes to `OLLAMA_MODEL` -- the catalog describes
another host, nothing in it has been measured, or routing is off.
