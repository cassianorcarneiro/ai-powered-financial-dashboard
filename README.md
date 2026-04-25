# 💰 AI Powered Financial Dashboard

A Python-based personal finance dashboard that turns CSV-stored transactions into interactive charts, financial indicators, and AI-driven written insights — all running locally via Docker, with **no data sent to the cloud**.

<p align="center">
  <img alt="Stack" src="https://img.shields.io/badge/Stack-Dash%20%2B%20Plotly%20%2B%20Ollama-blue?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white">
</p>

---

## ✨ Features

- 📊 **Interactive charts** — spending by category, payment method, monthly trends, installments starting/finishing
- 🤖 **AI insights** — a local LLM (via [Ollama](https://ollama.com)) reads aggregated metrics and produces a short financial commentary
- 💳 **Payment method management** — handle credit cards (with statement close / payment days) and debit accounts
- 📅 **Installment-aware** — splits multi-installment purchases into per-month records with automatic payment date computation
- 🔒 **Privacy-first** — all data stays on your machine; the LLM runs locally inside Docker
- 💾 **Persistent data** — your CSVs live on the host filesystem, untouched by container restarts

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

Two containers, one Docker network. The dashboard talks to Ollama at `http://ollama:11434`. Your data lives in `./data/` and is mounted into the dashboard container.

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
git clone https://github.com/cassianorcarneiro/<repo-name>.git
cd <repo-name>
```

### 2. Prepare your data folder

```
.
├── app.py
├── config.py
├── loading.html
├── requirements.txt
├── docker-compose.yaml
├── Dockerfile
├── .dockerignore
├── .env.example
└── data/
    ├── data.csv
    ├── categories.csv
    └── payment_methods.csv
```

The three CSV files inside `data/` **must exist**, even if empty. The app reads them at startup. Minimal headers expected:

| File | Required columns |
|------|------------------|
| `data.csv` | `Transaction Date;Payment Date;Label;Category;Amount;Installment;Payment Method;Hash;Record Timestamp;Ignore Entry` |
| `categories.csv` | `Name` |
| `payment_methods.csv` | `Name;Close Date;Payment Date;Type` |

> Use `;` as separator and UTF-8-with-BOM encoding (Excel-friendly).

### 3. (Optional) Configure environment

```bash
cp .env.example .env
# edit .env to change the model, port, or timezone
```

Defaults: model `llama3.2:3b`, port `8050`, timezone `America/Sao_Paulo`.

### 4. Build and start

```bash
docker compose up -d --build
```

This will:

1. Build the dashboard image
2. Pull the Ollama image
3. Start both containers in a private network
4. **Auto-pull the configured LLM** via the `model-puller` helper service (one-shot, runs once and exits)

The first run takes a few minutes — most of it is downloading the model (~2 GB for the default).

Verify everything is up:

```bash
docker compose ps
```

### 5. Open the dashboard

Visit **http://localhost:8050** in your browser.

The "Generate AI Insight" button at the top runs an analysis of the last 12 months. The first call may take 10–30 seconds while the model warms up; subsequent calls are faster.

---

## ⚙️ Configuration

All runtime configuration is done through environment variables, exposed via `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASHBOARD_PORT` | `8050` | Host port mapped to the dashboard |
| `OLLAMA_MODEL` | `llama3.2:3b` | Which LLM to use for insights |
| `TZ` | `America/Sao_Paulo` | Timezone for the "last update" timestamp |

### Choosing a model

| Model | Size | RAM needed | Quality | Best for |
|-------|------|-----------|---------|----------|
| `llama3.2:3b` ⭐ | ~2 GB | 4–6 GB | Good | Default, modest hardware |
| `llama3.1:8b` | ~4.7 GB | 8 GB | Better | More nuanced commentary, has the headroom |
| `mistral:7b` | ~4.1 GB | 8 GB | Strong reasoning | If you prefer Mistral's style |
| `phi3:mini` | ~2.3 GB | 4 GB | Decent | English-leaning, very fast |

Switch models by editing `OLLAMA_MODEL` in `.env`, then restart:

```bash
docker compose up -d
```

The `model-puller` service will fetch the new model automatically.

---

## 💾 Data persistence

| What | Where | Persisted? |
|------|-------|------------|
| Your CSVs | `./data/` (bind mount) | ✅ on the host filesystem |
| Ollama models | `ollama` named volume | ✅ across restarts |
| Container filesystem | inside the container | ❌ rebuilt each time |

Edit your CSVs directly with any tool (Excel, VS Code, `pandas`) — the dashboard re-reads them on every interaction.

To **completely reset** (including downloaded models):

```bash
docker compose down -v
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
docker compose logs -f ollama
```

### Restart only the dashboard (after editing the code)

```bash
docker compose up -d --build dashboard
```

### Manually pull a different model

```bash
docker exec -it ollama ollama pull mistral:7b
```

### List available models

```bash
docker exec -it ollama ollama list
```

---

## 🧯 Troubleshooting

**🔴 "ReadTimeout" or "Cannot connect to Ollama"**

The model is still loading on the first call. Wait 30 seconds and click "Generate AI Insight" again. If it persists:

```bash
docker compose logs ollama       # check Ollama is healthy
docker compose restart dashboard # warm restart of the dashboard
```

**🔴 Port 8050 is already in use**

Set a different port in `.env`:

```bash
DASHBOARD_PORT=8080
```

Then `docker compose up -d` and visit http://localhost:8080.

**🔴 Empty charts / "No data available"**

Make sure your `data/data.csv` has rows within the date filter range. The default filter is the current calendar year — adjust the date inputs at the top of the page.

**🔴 AI insight always shows the offline fallback**

Check that the model was pulled:

```bash
docker exec -it ollama ollama list
```

If the model isn't listed, pull it manually:

```bash
docker exec -it ollama ollama pull llama3.2:3b
```

**🔴 CSV changes don't appear in the dashboard**

The file is read on each callback, but the date filter may be excluding new rows. Reset filters with the ↺ button.

---

## 📁 Project structure

```
.
├── app.py                  # Dash app, callbacks, layout
├── config.py               # Colors, fonts, db paths, default model
├── loading.html            # Splash page opened on startup
├── requirements.txt        # Python dependencies
├── Dockerfile              # Dashboard image definition
├── docker-compose.yaml     # Two-service stack (dashboard + ollama + model-puller)
├── .dockerignore           # Excludes .git, __pycache__, venv from the build context
├── .env.example            # Template for runtime configuration
└── data/                   # ← your CSVs live here (bind-mounted into the container)
    ├── data.csv
    ├── categories.csv
    └── payment_methods.csv
```

---

## 🔐 Privacy

This project is designed to keep your financial data on your machine:

- ✅ CSVs never leave the host filesystem
- ✅ The LLM runs locally in the `ollama` container — no API calls to OpenAI, Anthropic, or anyone else
- ✅ The dashboard doesn't make outbound HTTP requests during normal operation
- ⚠️ The first run **does** require internet to pull the Docker image and the LLM model

After the initial setup, you can disconnect from the internet and the dashboard will keep working.

---

## 🛣️ Roadmap

- [ ] Excel import (in addition to CSV)
- [ ] Budget vs. actual tracking per category
- [ ] Recurring transaction detection
- [ ] Forecast next month's expenses based on installments already scheduled
- [ ] Multi-currency support
- [ ] Mobile-friendly layout

---

## 📜 License

MIT — see `LICENSE`.

## 👤 Author

**Cassiano Ribeiro Carneiro** — [@cassianorcarneiro](https://github.com/cassianorcarneiro)

---

> *Built for people who want financial insights without trusting their bank statements to a third-party API.*
