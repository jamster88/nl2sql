# nl2sql

## Synthetic data generator

A synthetic dataset generator for a grocery retail data model, along with the schema it implements, lives in [`data_gen/`](data_gen/README.md) -- see that README for details, setup, and usage.

Quick start:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r data_gen/requirements.txt
python data_gen/generate_data.py
```
