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

**Two tasks more since 7.0.** The ensemble (arch7) adds the Paraphraser and
the Judge to the router's tasks -- the Judge heavy always, and asked once a
question, before the vote (arch7.1). The scanner and the calibrator do not know
them yet, and a catalog -- schema 2, as every catalog built before 7.0 is --
is read with the two as unmeasured, so `OLLAMA_MODEL` answers them and the
routing table's notes say so. Routing on measured suitability only, that is
what any task nothing has measured gets. A catalog of a schema the agent
does not know is refused, as before.
