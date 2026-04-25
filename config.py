# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI POWERED FINANCIAL DASHBOARD
# CASSIANO RIBEIRO CARNEIRO
# V2
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

"""Static configuration for the AI Powered Financial Dashboard.

Values that should be overridable at runtime (e.g. the LLM model in Docker)
are read from environment variables in `app.py` with these defaults as
fallbacks.
"""

import os


class Config:
    # ----- Ollama -----
    ollama_timeout: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

    # ----- Locale -----
    timezone: str = os.getenv("TZ", "America/Sao_Paulo")

    # ----- Data file paths (relative to the project root / WORKDIR=/app) -----
    payment_methods_db: str = "data/payment_methods.csv"
    csv_db: str = "data/data.csv"
    categories_db: str = "data/categories.csv"

    # ----- Theme: grays -----
    gray_1: str = "#bbbbbb"
    gray_2: str = "#65737e"
    gray_3: str = "#aaaaaa"

    # ----- Theme: accent colors -----
    red_1: str = "#FBB4AE"
    green_1: str = "#CCEBC5"
    yellow_1: str = "#FED9A6"

    # ----- Theme: blues (primary palette) -----
    blue_1: str = "#B3CDEF"
    blue_2: str = "#58668b"
    blue_3: str = "#343d46"
    blue_4: str = "#3385c6"

    # ----- Typography -----
    fontsize_1: str = "15px"     # Modal body, buttons, entire table
    fontsize_2: str = "20px"     # Modal titles
    fontsize_3: str = "18px"     # Filter labels
    chart_fontsize_1: int = 12   # Charts

    # ----- Authentication -----
    request_password: bool = os.getenv("REQUEST_PASSWORD", "0") == "1"
    valid_users: dict[str, str] = {
        "user_test": "pass123",
    }
