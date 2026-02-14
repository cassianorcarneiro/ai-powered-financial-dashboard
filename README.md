# AI Powered Financial Insights

This project is a Python-based financial dashboard that uses Excel spreadsheets as the primary data source to generate interactive charts, financial indicators, and AI-driven textual insights. The goal is to provide an intuitive and intelligent way to monitor, analyze, and interpret financial data.

# Instructions

1) Prerequisites

Before anything else, the user must have the following installed:

Required

- Docker
- Docker Compose (already included in Docker Desktop)

Check in the terminal:

<pre>
docker --version
docker compose version
</pre>

If not installed:

Windows / macOS: https://www.docker.com/products/docker-desktop  
Linux: use your distribution’s package manager

2) Cloning the project

In the terminal:

<pre>
git clone <repository_url>
cd <local_repository>
</pre>

Expected structure (example):

<pre>
.
├── app.py
├── config.py
├── loading.html
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── data/
│   ├── transactions.csv
│   ├── categories.csv
│   └── payment_methods.csv
</pre>

The CSV files must exist (even if empty), because the app reads them at startup.

3) Install requirements

```bash
pip install -r requirements.txt
```

4) Starting the containers

From the project root:

<pre>
docker compose up -d --build
</pre>

This will:

- Build the dashboard container
- Pull the Ollama image
- Create an internal network between them

Check if everything is running:

<pre>
docker ps
</pre>

You should see something like:

<pre>
financial-dashboard  
ollama
</pre>

5) Downloading the AI model (required)

Ollama does not come with models by default.

Run once:

<pre>
docker exec -it ollama ollama pull llama3.1
</pre>

(Alternative models also work, e.g. mistral, phi3, etc.)

6) Accessing the Dashboard

Open your browser:

http://localhost:8050

You will see:

- Financial charts
- Transaction tables
- A panel at the top with the AI commentary (based on the last 12 months)

On the first run, the commentary may take a few seconds (initial model loading).

7) Where is the data stored?

The CSV files are stored outside the container, in the data/ folder.

Example:

<pre>
data/
├── transactions.csv
├── categories.csv
└── payment_methods.csv
</pre>

This ensures that:

- Your data is not lost when restarting containers
- You can manually edit the CSV files if you want

8) Stopping the system

To stop everything:

<pre>
docker compose down
</pre>

To stop without deleting Ollama data:

<pre>
docker compose down
</pre>

(The model volume is preserved automatically)

9) Common issues

❌ Commentary shows “ReadTimeout”

- The model is still loading
- Wait a few seconds and change any filter
- Or restart only the dashboard:

<pre>
docker restart financial-dashboard
</pre>

❌ “Cannot connect to Ollama”

Check:

<pre>
docker ps
</pre>

If the ollama container is not running:

<pre>
docker compose up -d ollama
</pre>

❌ Port 8050 is already in use

Edit docker-compose.yml:

ports:

"8080:8050"

Then access:

http://localhost:8080

10) Mental model of how it works

- Dash runs in financial-dashboard
- Ollama runs in ollama
- They communicate via the Docker network (http://ollama:11434)
- No external dependencies
- 100% offline AI
