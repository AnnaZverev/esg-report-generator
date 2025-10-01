import streamlit as st
import requests
import time
import re
import pandas as pd
import PyPDF2
import google.generativeai as genai
from io import BytesIO
import os


def extract_metrics_from_excel(uploaded_excel_file):
    """Извлекает ключевые метрики из загруженного Excel-файла."""
    try:
        df_env = pd.read_excel(uploaded_excel_file, sheet_name='Environmental', header=None)
        
        def get_metric(df, metric_name, year_column_index=10):
            try:
                row_index = df[df[0].str.contains(metric_name, na=False)].index[0]
                value = df.iloc[row_index, year_column_index]
                return f"{value:.3f}"
            except (IndexError, TypeError):
                return "н/д"

        quantitative_data = {
            "Выбросы Scope 1 (млн т CO2-экв.)": get_metric(df_env, "Direct (Scope 1) GHG emissions"),
            "Выбросы Scope 2 (млн т CO2-экв.)": get_metric(df_env, "Indirect (Scope 2) GHG emissions"),
            "Общий забор воды (млн м3)": get_metric(df_env, "Total water withdrawal")
        }
        return quantitative_data
    except Exception as e:
        st.error(f"Ошибка при обработке Excel файла: {e}")
        return None

def extract_narrative_from_pdf(uploaded_pdf_file, gemini_api_key):
    """Извлекает нарратив из PDF с помощью Gemini."""
    try:
        genai.configure(api_key=gemini_api_key)
        pdf_reader = PyPDF2.PdfReader(uploaded_pdf_file)
        pdf_text = "".join(page.extract_text() for page in pdf_reader.pages)

        model = genai.GenerativeModel('gemini-1.5-pro-latest')

        prompts_en = {
            "Governance": "Analyze the text and briefly describe the role of the Board of Directors and management in overseeing and managing climate-related risks.",
            "Strategy": "Find the description of climate-related risks and opportunities for the company. Mention if a scenario analysis was conducted.",
            "Risk Management": "Describe the company's processes for identifying, assessing, and managing climate-related risks.",
            "Metrics and Targets": "Find and list the company's key future climate-related targets."
        }

        narrative_data = {}
        status_placeholder = st.empty()
        for section, prompt in prompts_en.items():
            status_placeholder.info(f"🤖 Анализ PDF: извлечение раздела '{section}'...")
            response = model.generate_content(
                "You are a professional ESG analyst. Provide a clear, structured summary in ENGLISH based on the user's request.\n\n"
                f"REQUEST: {prompt}\n\n"
                f"SOURCE TEXT (in Russian):\n{pdf_text}"
            )
            narrative_data[section] = response.text
        status_placeholder.success("✅ Анализ PDF завершен.")
        return narrative_data
    except Exception as e:
        st.error(f"Ошибка при работе с Gemini API: {e}")
        return None

def build_gamma_prompt(company_name, reporting_year, quantitative_data, narrative_data):
    """Собирает финальный промпт для Gamma."""
    def format_metric(label, value, unit):
        try:
            float(value)
            return f"- {label}: {value} {unit}"
        except (ValueError, TypeError):
            return f"- {label}: Not available"

    metrics_text = "\n".join([
        format_metric("Scope 1 GHG Emissions", quantitative_data.get('Выбросы Scope 1 (млн т CO2-экв.)'), "million tCO2e"),
        format_metric("Scope 2 GHG Emissions", quantitative_data.get('Выбросы Scope 2 (млн т CO2-экв.)'), "million tCO2e"),
        format_metric("Total Water Withdrawal", quantitative_data.get('Общий забор воды (млн м3)'), "million m³")
    ])

    return f"""
# TOPIC: Climate-Related Financial Disclosure (TCFD) Report for {company_name}, {reporting_year}.
---
## SECTION 1: Governance
**Key points to elaborate on:**
- {narrative_data.get("Governance", "No specific data found.")}
---
## SECTION 2: Strategy
**Key points to elaborate on:**
- {narrative_data.get("Strategy", "No specific data found.")}
---
## SECTION 3: Risk Management
**Key points to elaborate on:**
- {narrative_data.get("Risk Management", "No specific data found.")}
---
## SECTION 4: Metrics and Targets
**Key points to elaborate on:**
- Present key metrics and strategic targets.
- Use the following data:
  {metrics_text}
  - Narrative on targets: "{narrative_data.get("Metrics and Targets", "No specific data found.")}"
"""

def generate_with_gamma(gamma_api_key, gamma_prompt, company_name):
    """Вызывает Gamma API, отслеживает статус и возвращает байты PDF файла."""
    headers = {"X-API-KEY": gamma_api_key, "Content-Type": "application/json"}
    payload = {
        "inputText": gamma_prompt,
        "format": "document",
        "exportAs": "pdf",
        "textMode": "condense",
        "additionalInstructions": "It is critically important that all text fits neatly within the page boundaries. Adjust layouts or slightly condense the text on each page to prevent any overflow.",
        "themeName": "ESG_Anna", # Ваша кастомная тема
        "textOptions": {"language": "en", "amount": "detailed"},
        "imageOptions": {"source": "aiGenerated", "style": "photorealistic, corporate, clean"},
        "cardOptions": {"dimensions": "a4"}
    }
    
    generation_endpoint = "https://public-api.gamma.app/v0.2/generations"
    
    response = requests.post(generation_endpoint, headers=headers, json=payload)
    response.raise_for_status()
    
    generation_data = response.json()
    generation_id = generation_data.get("generationId")
    if not generation_id:
        raise ValueError(f"Не удалось получить 'generationId'. Ответ: {generation_data}")

    status_placeholder = st.empty()
    status_placeholder.info(f"✅ Запрос принят Gamma. ID задачи: {generation_id}. Начинаю проверку статуса...")
    
    status_endpoint = f"https://public-api.gamma.app/v0.2/generations/{generation_id}"
    download_url = None

    for i in range(25):
        status_response = requests.get(status_endpoint, headers=headers)
        status_response.raise_for_status()
        status_data = status_response.json()
        current_status = status_data.get("status")
        status_placeholder.info(f"🎨 Статус генерации в Gamma: {current_status}... ({i+1}/25)")
        
        if current_status == "completed":
            download_url = status_data.get("exportUrl")
            if download_url:
                status_placeholder.success("✅ Документ готов к скачиванию!")
                break
        elif current_status == "failed":
            raise Exception(f"Ошибка генерации. Детали: {status_data.get('error')}")
        time.sleep(10)

    if download_url:
        pdf_response = requests.get(download_url)
        pdf_response.raise_for_status()
        return pdf_response.content
    else:
        st.error("Не удалось получить ссылку на скачивание после завершения цикла.")
        return None

# --- Интерфейс Streamlit ---

st.set_page_config(page_title="ESG Report Generator", layout="wide")
st.title("🤖 Генератор ESG-отчетов (TCFD)")
st.markdown("Загрузите отчет в формате GRI (PDF + Excel), и приложение сгенерирует для вас новый отчет в формате TCFD.")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GAMMA_API_KEY = os.environ.get("GAMMA_API_KEY")

if not (GEMINI_API_KEY and GAMMA_API_KEY):
    st.sidebar.warning("API ключи не найдены в окружении. Введите их вручную для локального теста.")
    GEMINI_API_KEY = st.sidebar.text_input("Gemini API Key", type="password")
    GAMMA_API_KEY = st.sidebar.text_input("Gamma API Key", type="password")

col1, col2 = st.columns(2)

with col1:
    company_name_input = st.text_input("Название компании (для заголовков)", "Polyus PJSC")
    reporting_year_input = st.text_input("Отчетный год", "2023")
    
with col2:
    pdf_file = st.file_uploader("1. Загрузите PDF отчет (GRI)", type="pdf")
    excel_file = st.file_uploader("2. Загрузите Excel с данными (GRI)", type="xlsx")

if 'generated_pdf' not in st.session_state:
    st.session_state.generated_pdf = None

if st.button("🚀 Сгенерировать TCFD отчет", type="primary"):
    if pdf_file and excel_file and GEMINI_API_KEY and GAMMA_API_KEY:
        with st.spinner("Пожалуйста, подождите, идет магия... Это может занять несколько минут."):
            # Шаг 1: Извлечение данных
            quantitative = extract_metrics_from_excel(excel_file)
            narrative = extract_narrative_from_pdf(pdf_file, GEMINI_API_KEY)

            if quantitative and narrative:
                # Шаг 2: Сборка промпта для Gamma
                st.info("📝 Данные извлечены, собираю финальный промпт для Gamma...")
                gamma_prompt = build_gamma_prompt(company_name_input, reporting_year_input, quantitative, narrative)
                
                # Шаг 3: Генерация в Gamma
                pdf_bytes = generate_with_gamma(GAMMA_API_KEY, gamma_prompt, company_name_input)
                
                if pdf_bytes:
                    st.session_state.generated_pdf = pdf_bytes
    else:
        st.error("Пожалуйста, загрузите оба файла и убедитесь, что API ключи введены.")

if st.session_state.generated_pdf:
    st.success("🎉 Ваш отчет готов!")
    sanitized_company_name = re.sub(r'[<>:"/\\|?*«»]', '', company_name_input).replace(' ', '_')
    st.download_button(
        label="📥 Скачать TCFD отчет (PDF)",
        data=st.session_state.generated_pdf,
        file_name=f"TCFD_Report_{sanitized_company_name}.pdf",
        mime="application/pdf"

    )

