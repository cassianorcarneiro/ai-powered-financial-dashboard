# ai-powered-financial-insights
This project is a Python-based financial dashboard that uses Excel spreadsheets as the primary data source to generate interactive charts, financial indicators, and AI-driven textual insights. The goal is to provide an intuitive and intelligent way to monitor, analyze, and interpret financial data.

# Instructions

1) Prerequisites

Before anything else, the user must have the following installed:

Required

- Docker
- Docker Compose (already included in Docker Desktop)

Check in the terminal:

docker --version
docker compose version

If not installed:

Windows / macOS: https://www.docker.com/products/docker-desktop  
Linux: use your distribution’s package manager

2) Cloning the project

In the terminal:

git clone <repository_url>
cd <local_repository>

Expected structure (example):

.
├── app.py

├── config.py

├── requirements.txt

├── docker-compose.yml

├── Dockerfile

├── data/

│   ├── transactions.csv

│   ├── categories.csv

│   └── payment_methods.csv

The CSV files must exist (even if empty), because the app reads them at startup.

3) Starting the containers

From the project root:

docker compose up -d --build

This will:

- Build the dashboard container
- Pull the Ollama image
- Create an internal network between them

Check if everything is running:

docker ps

You should see something like:

financial-dashboard  
ollama

4) Downloading the AI model (required)

Ollama does not come with models by default.

Run once:

docker exec -it ollama ollama pull llama3.1

(Alternative models also work, e.g. mistral, phi3, etc.)

5) Accessing the Dashboard

Open your browser:

http://localhost:8050

You will see:

- Financial charts
- Transaction tables
- A panel at the top with the AI commentary (based on the last 12 months)

On the first run, the commentary may take a few seconds (initial model loading).

6) Where is the data stored?

The CSV files are stored outside the container, in the data/ folder.

Example:

data/

├── transactions.csv

├── categories.csv

└── payment_methods.csv

This ensures that:

- Your data is not lost when restarting containers
- You can manually edit the CSV files if you want

7) Stopping the system

To stop everything:

docker compose down

To stop without deleting Ollama data:

docker compose down

(The model volume is preserved automatically)

8) Common issues

❌ Commentary shows “ReadTimeout”

- The model is still loading
- Wait a few seconds and change any filter
- Or restart only the dashboard:

docker restart financial-dashboard

❌ “Cannot connect to Ollama”

Check:

docker ps

If the ollama container is not running:

docker compose up -d ollama

❌ Port 8050 is already in use

Edit docker-compose.yml:

ports:
  - "8080:8050"

Then access:

http://localhost:8080

9) Mental model of how it works

- Dash runs in financial-dashboard
- Ollama runs in ollama
- They communicate via the Docker network (http://ollama:11434)
- No external dependencies
- 100% offline AI
=======
>>>>>>> a2368e9 (Initial commit)
