# 💰 AI Powered Financial Dashboard

> Personal finance dashboard with local AI insights — your data never leaves your machine.

A Python-based personal finance dashboard that turns CSV-stored transactions into interactive charts, financial indicators, and AI-driven written insights. Everything runs locally via Docker, with **no data sent to the cloud**.

<p align="center">
  <img alt="Stack" src="https://img.shields.io/badge/Stack-Dash%20%2B%20Plotly%20%2B%20Ollama-blue?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white">
</p>

---

## 📦 Features

- 📊 **Interactive charts** — spending by category, payment method, monthly trends, installments starting/finishing
- 🤖 **AI insights** — a local LLM (via [Ollama](https://ollama.com)) reads aggregated metrics and produces a short financial commentary
- 💳 **Payment method management** — handle credit cards (with statement close / payment days) and debit accounts
- 📅 **Installment-aware** — splits multi-installment purchases into per-month records with automatic payment date computation
- 🔒 **Privacy-first** — all data stays on your machine; the LLM runs locally
- 💾 **Persistent data** — your CSVs live on the host filesystem, untouched by container restarts
- 🛟 **Graceful degradation** — when the model is unavailable or out of memory, a deterministic summary is shown instead of an error

---

## 🏗️ Architecture

```
┌─────────────────────────┐         ┌──────────────────┐
│   financial-dashboard   │  HTTP   │      ollama      │
│   (Dash + Plotly)       │ ──────▶ │   (local LLM)    │
│   port 8050             │         │   port 11434     │
└────────────┬────────────┘         └──────────────────┘
             │
             ▼
       ┌───────────┐
       │  ./data/  │   ← CSVs persisted on the host
       └───────────┘
```

The dashboard reaches Ollama over HTTP at the address given by `OLLAMA_URL`. That address can point either at a bundled Ollama container or at an instance you already run on the host — see [Choosing how to run Ollama](#-choosing-how-to-run-ollama). Your data lives in `./data/` and is mounted into the dashboard container.

---

## 📋 Prerequisites

- **Docker Desktop** (Windows/macOS) or **Docker Engine + Compose plugin** (Linux)
  - Verify: `docker --version` and `docker compose version`
- **~8 GB free disk** (for the LLM model and images)
- **~6 GB free RAM** (for `llama3.2:3b`; more for larger models)
- **Internet** for the first run (pulling the Docker image and the LLM model)

> Don't have Docker? Get it at [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop).

---

## 🚀 Quick start

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/ai-powered-financial-dashboard.git
cd ai-powered-financial-dashboard
```

### 2. Prepare your data folder

```
.
├── app.py
├── config.py
├── storage.py
├── metrics.py
├── insights.py
├── charts.py
├── layout.py
├── requirements.txt
├── docker-compose.yaml
├── Dockerfile
├── .dockerignore
├── .env.example
├── examples/
│   ├── data.example.csv
│   ├── categories.example.csv
│   └── payment_methods.example.csv
└── data/               # ← your real files go here; kept out of git
    ├── data.csv
    ├── categories.csv
    └── payment_methods.csv
```

The three CSV files inside `data/` are **created automatically on first run** if they are missing, with the correct headers. To start from the bundled samples instead:

```bash
cp examples/data.example.csv            data/data.csv
cp examples/categories.example.csv      data/categories.csv
cp examples/payment_methods.example.csv data/payment_methods.csv
```

Keeping the samples in `examples/` rather than inside `data/` is deliberate: `data/*.csv` is gitignored, and a stray `rm data/*.csv` while clearing your own records would otherwise take the samples down with it.

Expected headers:

| File | Required columns |
|------|------------------|
| `data.csv` | `Transaction Date;Payment Date;Label;Category;Amount;Installment;Payment Method;Hash;Record Timestamp;Ignore Entry` |
| `categories.csv` | `Name` |
| `payment_methods.csv` | `Name;Close Date;Payment Date;Type` |

> Use `;` as separator and UTF-8-with-BOM encoding (Excel-friendly).
> **Sign convention:** expenses are negative amounts, income is positive.

### 3. Configure environment

```bash
cp .env.example .env
# edit .env to set the Ollama endpoint, model, port, timezone or currency
```

### 4. Build and start

Pick one of the two modes below, then verify with `docker compose ps`.

**Bundled Ollama** — the stack brings up its own Ollama and pulls the model for you:

```bash
docker compose --profile bundled-ollama up -d --build
```

**Existing Ollama** — reuse an instance already running on your machine:

```bash
docker compose up -d --build
```

The first run takes a few minutes, mostly downloading the model (~2 GB for the default).

### 5. Open the dashboard

Visit **http://localhost:8050**.

The **Generate AI Insight** button analyzes the last 12 months. The first call may take 10–30 seconds while the model warms up; later calls are faster.

---

## 🧠 Choosing how to run Ollama

| | Bundled Ollama | Existing Ollama |
|---|---|---|
| Command | `docker compose --profile bundled-ollama up -d` | `docker compose up -d` |
| `OLLAMA_URL` | `http://ollama:11434` | host address (see below) |
| Model pulled automatically | ✅ | ❌ — run `ollama pull <model>` yourself |
| GPU access | CPU only unless the `deploy` block in `docker-compose.yaml` is uncommented | inherits whatever the host instance already has |

**Reusing a host instance is usually the better option when that instance already has GPU access.** A containerized Ollama without an explicit device reservation runs on CPU, which is markedly slower and wastes a GPU that is already available.

Two things are required for it:

1. **The host Ollama must listen on an address the container can reach.** By default it binds to `127.0.0.1`, which is unreachable from inside a container. On a systemd host:

   ```bash
   sudo systemctl edit ollama.service
   ```

   ```ini
   [Service]
   Environment="OLLAMA_HOST=172.17.0.1:11434"
   ```

   ```bash
   sudo systemctl daemon-reload && sudo systemctl restart ollama.service
   ```

   Binding to the Docker bridge address rather than `0.0.0.0` keeps the API off your LAN and off any VPN interface. Ollama has no authentication, so the narrower bind is worth the extra step. Confirm the bridge address with `ip addr show docker0`.

2. **Point `OLLAMA_URL` at that address** in `.env`:

   | Host OS | Value |
   |---------|-------|
   | Linux | `http://172.17.0.1:11434` |
   | macOS / Windows | `http://host.docker.internal:11434` |

---

## ⚙️ Configuration

All runtime configuration is done through environment variables, exposed via `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASHBOARD_PORT` | `8050` | Host port mapped to the dashboard |
| `OLLAMA_URL` | `http://ollama:11434` | Where the dashboard reaches Ollama |
| `OLLAMA_MODEL` | `llama3.2:3b` | Which LLM to use for insights |
| `OLLAMA_TIMEOUT` | `120` | Seconds to wait for a completion |
| `TZ` | `UTC` | Timezone for the "last update" timestamp |
| `CURRENCY` | `BRL` | Currency label passed to the model |
| `REQUEST_PASSWORD` | `0` | Set to `1` to require basic authentication |
| `DASHBOARD_USERS` | *(empty)* | `user:password` pairs, comma-separated |
| `LOG_LEVEL` | `INFO` | Python logging level |

### Choosing a model

| Model | Size | RAM needed | Quality | Best for |
|-------|------|-----------|---------|----------|
| `llama3.2:3b` ⭐ | ~2 GB | 4–6 GB | Good | Default, modest hardware |
| `llama3.1:8b` | ~4.7 GB | 8 GB | Better | More nuanced commentary, if you have the headroom |
| `mistral:7b` | ~4.1 GB | 8 GB | Strong reasoning | If you prefer Mistral's style |
| `phi3:mini` | ~2.3 GB | 4 GB | Decent | English-leaning, very fast |

Switch models by editing `OLLAMA_MODEL` in `.env`, then restart:

```bash
docker compose up -d
```

With the bundled profile the `model-puller` service fetches the new model automatically. With a host instance, pull it yourself: `ollama pull mistral:7b`.

---

## 💾 Data persistence

| What | Where | Persisted? |
|------|-------|------------|
| Your CSVs | `./data/` (bind mount) | ✅ on the host filesystem |
| Ollama models | `ollama` named volume (bundled profile) | ✅ across restarts |
| Container filesystem | inside the container | ❌ rebuilt each time |

Edit your CSVs directly with any tool (Excel, VS Code, `pandas`) — the dashboard re-reads them on every interaction. Writes from the app are atomic, so an interrupted save cannot leave a half-written file behind.

`data/*.csv` is listed in `.gitignore`: your transaction history is never committed. The reference samples live in `examples/` instead, outside `data/`, so clearing your own files never touches them.

To **completely reset** (including downloaded models):

```bash
docker compose --profile bundled-ollama down -v
```

To stop **without** losing data or models:

```bash
docker compose down
```

---

## 🛠️ Common operations

### View logs

```bash
docker compose logs -f dashboard
docker compose logs -f ollama        # bundled profile only
```

### Restart only the dashboard (after editing the code)

```bash
docker compose up -d --build dashboard
```

### Check container health

```bash
docker compose ps
curl http://localhost:8050/healthz
```

### List or pull models

```bash
# Bundled profile
docker exec -it ollama ollama list
docker exec -it ollama ollama pull mistral:7b

# Host instance
ollama list
ollama pull mistral:7b
```

### Run locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
OLLAMA_URL=http://localhost:11434 python app.py
```

---

## 🔧 Troubleshooting

**🔴 The insight panel says "Ollama unavailable"**

The dashboard cannot reach `OLLAMA_URL`. From inside the container:

```bash
docker compose exec dashboard curl -sS "$OLLAMA_URL/api/tags"
```

If this fails while `curl` from the host succeeds, the host Ollama is bound to `127.0.0.1` only. See [Choosing how to run Ollama](#-choosing-how-to-run-ollama).

**🔴 The insight panel says "Model busy"**

Ollama could not load the model because GPU or system memory is exhausted, usually because another workload is holding it. The deterministic summary is shown in the meantime; retry once memory frees up, or switch `OLLAMA_MODEL` to a smaller model.

**🔴 The insight panel says "Timeout"**

The first call after startup is slow while the model loads. Retry, or raise `OLLAMA_TIMEOUT`.

**🔴 "Model not available on the Ollama server"**

Pull it: `ollama pull llama3.2:3b` (or `docker exec -it ollama ollama pull llama3.2:3b` with the bundled profile).

**🔴 Port 8050 is already in use**

Set a different port in `.env`:

```bash
DASHBOARD_PORT=8080
```

Then `docker compose up -d` and visit http://localhost:8080.

**🔴 Empty charts / "No data in the selected period"**

The default filter is the current calendar year. Adjust the date inputs, or reset them with the ↺ button.

**🔴 The app reports a missing column**

An existing CSV does not match the expected header. Compare it against the table in [Prepare your data folder](#2-prepare-your-data-folder); the error message names the missing columns.

---

## 📁 Project structure

```
.
├── app.py                  # Dash app, callbacks, record generation, entry point
├── config.py               # Environment-driven settings, theme, CSV schemas
├── storage.py              # Atomic CSV reads/writes, bootstrap, validation
├── metrics.py              # Trailing 12-month aggregations
├── insights.py             # Ollama client, prompt, fallback summary
├── charts.py               # Plotly figure factory
├── layout.py               # Dash component tree
├── requirements.txt        # Python dependencies
├── Dockerfile              # Dashboard image (non-root, Gunicorn)
├── docker-compose.yaml     # Dashboard, plus optional bundled Ollama
├── .dockerignore           # Keeps data, secrets and VCS out of the build context
├── .env.example            # Template for runtime configuration
├── .gitignore              # Keeps financial data and .env out of version control
├── examples/               # Reference CSVs — copy into data/ to try the app
│   ├── data.example.csv
│   ├── categories.example.csv
│   └── payment_methods.example.csv
└── data/                   # ← your CSVs live here (bind-mounted into the container)
    ├── data.csv
    ├── categories.csv
    └── payment_methods.csv
```

---

## 🔐 Privacy and security

This project is designed to keep your financial data on your machine:

- ✅ CSVs never leave the host filesystem and are excluded from version control
- ✅ The LLM runs locally — no API calls to any hosted provider
- ✅ Only aggregated metrics are sent to the model, never individual transactions
- ✅ The container runs as an unprivileged user
- ⚠️ The first run **does** require internet to pull the Docker image and the LLM model
- ⚠️ Basic authentication transmits credentials in clear text. Enable it only behind TLS or on a trusted network such as a private VPN, and never commit real credentials

After the initial setup, you can disconnect from the internet and the dashboard will keep working.

---

## 🛣️ Roadmap

- [ ] Excel import (in addition to CSV)
- [ ] Budget vs. actual tracking per category
- [ ] Recurring transaction detection
- [ ] Forecast next month's expenses based on installments already scheduled
- [ ] Multi-currency support
- [ ] Automated test suite in CI

---

## 📜 License

MIT — see `LICENSE` file.

---

### 🤖 AI Assistance Disclosure

The codebase architecture, organizational structure, and stylistic formatting of this repository were refactored and optimized leveraging [Claude](https://www.anthropic.com/claude) by Anthropic. All core business logic and intellectual property remain the work of the repository authors and are governed by the project's license.

---

> *Built for people who want financial insights without trusting their bank statements to a third-party API.*
