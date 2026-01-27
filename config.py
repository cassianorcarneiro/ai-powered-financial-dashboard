# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI POWERED FINANCIAL DASHBOARD
# CASSIANO RIBEIRO CARNEIRO
# V1
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

class Config:

    ollama_timeout = 120
    ollama_model = "llama3.2:3b"
    
    timezone = "America/Sao_Paulo"

    payment_methods_db = "data/payments_methods.csv"
    csv_db = "data/data.csv"
    categories_db = "data/categories.csv"

    gray_1 = "#bbbbbb"
    gray_2 = "#65737e"
    gray_3 = "#aaaaaa"
    red_1 = "#FBB4AE"
    green_1 = "#CCEBC5"
    yellow_1 = "#FED9A6"
    blue_1 = "#B3CDEF"
    blue_2 = "#58668b"
    blue_3 = "#343d46"
    blue_4 = "#3385c6"

    fontsize_1 = "15px"     # Modal body, buttons, entire table
    fontsize_2 = "20px"     # Modal titles
    fontsize_3 = "18px"     # Filter labels
    chart_fontsize_1 = 12   # Charts

    request_password = False

    valid_users = {
    "user_test": "pass123"
    }