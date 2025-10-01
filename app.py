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

def extract_data_from_pdf(uploaded_pdf_file, gemini_api_key, excel_provided):
    """
    Извлекает данные из PDF, основываясь на 11 рекомендуемых раскрытиях TCFD.
    Если Excel не предоставлен, пытается извлечь и количественные данные.
    """
    try:
        genai.configure(api_key=gemini_api_key)
        pdf_reader = PyPDF2.PdfReader(uploaded_pdf_file)
        pdf_text = "".join(page.extract_text() for page in pdf_reader.pages)
        model = genai.GenerativeModel('gemini-1.5-pro-latest')

        # --- ИЗМЕНЕНИЕ: Детализированные промпты по 11 раскрытиям TCFD ---
        prompts_en_tcfd = {
            # Governance (2 disclosures)
            "Governance a) Board's oversight": "Describe the board’s oversight of climate-related risks and opportunities.",
            "Governance b) Management's role": "Describe management’s role in assessing and managing climate-related risks and opportunities.",
            
            # Strategy (3 disclosures)
            "Strategy a) Identified risks and opportunities": "Describe the climate-related risks and opportunities the organization has identified over the short, medium, and long term.",
            "Strategy b) Impact on organization": "Describe the impact of climate-related risks and opportunities on the organization’s businesses, strategy, and financial planning.",
            "Strategy c) Resilience of strategy": "Describe the resilience of the organization’s strategy, taking into consideration different climate-related scenarios, including a 2°C or lower scenario.",
            
            # Risk Management (3 disclosures)
            "Risk Management a) Risk identification processes": "Describe the organization’s processes for identifying and assessing climate-related risks.",
            "Risk Management b) Risk management processes": "Describe the organization’s processes for managing climate-related risks.",
            "Risk Management c) Integration into overall risk management": "Describe how processes for identifying, assessing, and managing climate-related risks are integrated into the organization’s overall risk management.",
            
            # Metrics and Targets (3 disclosures)
            "Metrics and Targets a) Metrics used": "Disclose the metrics used by the organization to assess climate-related risks and opportunities.",
            "Metrics and Targets b) GHG Emissions": "Disclose Scope 1, Scope 2, and, if appropriate, Scope 3 greenhouse gas (GHG) emissions, and the related risks.",
            "Metrics and Targets c) Targets used": "Describe the targets used by the organization to manage climate-related risks and opportunities and performance against targets."
        }
        
        # Если Excel не предоставлен, мы все еще можем попытаться извлечь ключевые цифры
        if not excel_provided:
            prompts_en_tcfd['Quantitative Data (if available)'] = (
                "Find the following key metrics for the most recent reporting year in the text. "
                "Provide only the numerical value. If a metric is not found, return 'Not available'.\n"
                "- Scope 1 GHG Emissions (in million tCO2e):\n"
                "- Scope 2 GHG Emissions (in million tCO2e):\n"
            )

        extracted_data = {}
        status_placeholder = st.empty()
        total_prompts = len(prompts_en_tcfd)
        
        for i, (section, prompt) in enumerate(prompts_en_tcfd.items()):
            status_placeholder.info(f"🤖 Анализ PDF: извлечение раскрытия '{section}' ({i+1}/{total_prompts})...")
            response = model.generate_content(
                "You are a professional ESG analyst. Your task is to analyze the following sustainability report text "
                "and provide a clear, structured summary in ENGLISH based on the user's request. Focus only on the information relevant to the specific request.\n\n"
                f"REQUEST: {prompt}\n\n"
                f"SOURCE TEXT (in Russian):\n{pdf_text}"
            )
            extracted_data[section] = response.text
        
        status_placeholder.success("✅ Детализированный анализ PDF по 11 раскрытиям TCFD завершен.")
        return extracted_data
    except Exception as e:
        st.error(f"Ошибка при работе с Gemini API: {e}")
        return {}

def build_gamma_prompt(company_name, reporting_year, quantitative_data, narrative_data):
    """
    Собирает продвинутый, детализированный промпт для Gamma,
    который умеет обрабатывать отсутствующую информацию.
    """
    
    # --- Хелпер-функция для обработки отсутствующих данных ---
    def process_disclosure(disclosure_key, default_text="Information not disclosed in the source report."):
        """Проверяет, есть ли данные. Если нет, возвращает стандартную фразу."""
        content = narrative_data.get(disclosure_key, "").strip()
        # Считаем, что данных нет, если ответ пустой или содержит стандартные фразы о ненаходке
        if not content or "not found" in content.lower() or "not provide" in content.lower():
            return default_text
        return content

    # --- Подготовка всех 11 раскрытий с помощью хелпера ---
    gov_a = process_disclosure("Governance a) Board's oversight")
    gov_b = process_disclosure("Governance b) Management's role")
    
    strat_a = process_disclosure("Strategy a) Identified risks and opportunities")
    strat_b = process_disclosure("Strategy b) Impact on organization")
    strat_c = process_disclosure("Strategy c) Resilience of strategy")

    risk_a = process_disclosure("Risk Management a) Risk identification processes")
    risk_b = process_disclosure("Risk Management b) Risk management processes")
    risk_c = process_disclosure("Risk Management c) Integration into overall risk management")

    metrics_a = process_disclosure("Metrics and Targets a) Metrics used")
    metrics_b = process_disclosure("Metrics and Targets b) GHG Emissions") # Этот пункт мы дополним цифрами
    metrics_c = process_disclosure("Metrics and Targets c) Targets used")

    # --- Формирование блока с количественными данными ---
    def format_metric(label, value, unit):
        if value and str(value).lower() != "not available":
            return f"- **{label}:** {value} {unit}"
        return f"- **{label}:** Not available"

    metrics_text = "\n".join([
        format_metric("Scope 1 GHG Emissions", quantitative_data.get('Scope 1 GHG Emissions'), "million tCO2e"),
        format_metric("Scope 2 GHG Emissions", quantitative_data.get('Scope 2 GHG Emissions'), "million tCO2e"),
    ])

    # --- Финальная сборка промпта ---
    
    final_prompt = f"""
# TASK: Create a professional TCFD Report for {company_name}, {reporting_year}.

**ROLE:** You are an expert ESG analyst from a top-tier consulting firm. Your task is to synthesize the provided raw data points into a polished, investor-grade report that follows the TCFD framework.

**IMPORTANT INSTRUCTION:** If a data point is marked as 'Information not disclosed...', you must explicitly and professionally state this in the final report. DO NOT ignore missing data. Frame it as a finding of your analysis.

---
---

# TCFD Report: {company_name} ({reporting_year})

---
## 1. Governance
*Disclosing the organization’s governance around climate-related risks and opportunities.*

**a) Board’s Oversight:**
{gov_a}

**b) Management’s Role:**
{gov_b}

---
## 2. Strategy
*Disclosing the actual and potential impacts of climate-related risks and opportunities on the organization’s businesses, strategy, and financial planning.*

**a) Identified Risks and Opportunities:**
{strat_a}

**b) Impact on Business, Strategy, and Financial Planning:**
{strat_b}

**c) Resilience of Strategy (Scenario Analysis):**
{strat_c}

---
## 3. Risk Management
*Disclosing how the organization identifies, assesses, and manages climate-related risks.*

**a) Risk Identification and Assessment Processes:**
{risk_a}

**b) Risk Management Processes:**
{risk_b}

**c) Integration into Overall Risk Management:**
{risk_c}

---
## 4. Metrics and Targets
*Disclosing the metrics and targets used to assess and manage relevant climate-related risks and opportunities.*

**a) Metrics Used for Assessment:**
{metrics_a}

**b) Greenhouse Gas (GHG) Emissions:**
{metrics_b}
Key reported emissions data includes:
{metrics_text}

**c) Targets and Performance:**
{metrics_c}
"""
    return final_prompt
def generate_with_gamma(gamma_api_key, gamma_prompt, company_name):
    """Вызывает Gamma API, отслеживает статус и возвращает байты PDF файла."""
    headers = {"X-API-KEY": gamma_api_key, "Content-Type": "application/json"}
    payload = {
        "inputText": gamma_prompt,
        "format": "document",
        "exportAs": "pdf",
        "textMode": "condense",
        "additionalInstructions": "It is critically important that all text fits neatly within the page boundaries. Adjust layouts or slightly condense the text on each page to prevent any overflow.",
        "themeName": "ESG_Anna",
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
    status_placeholder.info(f"✅ Запрос принят. ID задачи: {generation_id}. Начинаю генерацию...")
    
    status_endpoint = f"https://public-api.gamma.app/v0.2/generations/{generation_id}"
    download_url = None

    for i in range(25): 

        status_placeholder.info(f"🎨 Генерирую отчет по требованиям TCFD... Шаг {i+1} из максимум 25")
        
        status_response = requests.get(status_endpoint, headers=headers)
        status_response.raise_for_status()
        status_data = status_response.json()
        current_status = status_data.get("status")
        
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
    
    # 1: Главная проверка только на обязательный PDF-файл и ключи
    if pdf_file and GEMINI_API_KEY and GAMMA_API_KEY:
        with st.spinner("Пожалуйста, подождите, идет магия... Это может занять несколько минут."):
            
            # 2. Создаем флаг, чтобы знать, был ли загружен Excel
            excel_provided = bool(excel_file)
            
            # 3. Извлекаем все данные из PDF (текст и, возможно, цифры, если Excel нет)
            all_pdf_data = extract_data_from_pdf(pdf_file, GEMINI_API_KEY, excel_provided)
            
            # Создаем пустой словарь для количественных данных
            quantitative = {}

            # 4. Решаем, откуда брать количественные данные
            if excel_provided:
                st.info("Найден Excel файл, извлекаю точные количественные данные...")
                # Если есть Excel - берем цифры из него (они точнее)
                quantitative = extract_metrics_from_excel(excel_file)
            else:
                st.info("Excel файл не загружен, использую данные, найденные в PDF...")
                # Если Excel нет - пытаемся собрать цифры из ответа Gemini
                quant_text = all_pdf_data.get('Quantitative Data (if available)', '')
                lines = quant_text.split('\n')
                for line in lines:
                    if "Scope 1" in line: quantitative["Scope 1 GHG Emissions"] = re.search(r'(\d+\.?\d*)', line).group(1) if re.search(r'(\d+\.?\d*)', line) else "Not available"
                    if "Scope 2" in line: quantitative["Scope 2 GHG Emissions"] = re.search(r'(\d+\.?\d*)', line).group(1) if re.search(r'(\d+\.?\d*)', line) else "Not available"

            # 5. Проверяем, что извлечение прошло успешно, и запускаем генерацию
            if all_pdf_data:
                # Собираем промпт для Gamma, используя все извлеченные данные
                gamma_prompt = build_gamma_prompt(company_name_input, reporting_year_input, quantitative, all_pdf_data)
                
                # Запускаем генерацию в Gamma
                pdf_bytes = generate_with_gamma(GAMMA_API_KEY, gamma_prompt)
                
                if pdf_bytes:
                    st.session_state.generated_pdf = pdf_bytes
    else:
        # Если обязательный PDF-файл не загружен, выводим ошибку
        st.error("Пожалуйста, загрузите PDF-файл и убедитесь, что API ключи настроены.")
        
if st.session_state.generated_pdf:
    st.success("🎉 Ваш отчет готов!")
    sanitized_company_name = re.sub(r'[<>:"/\\|?*«»]', '', company_name_input).replace(' ', '_')
    st.download_button(
        label="📥 Скачать TCFD отчет (PDF)",
        data=st.session_state.generated_pdf,
        file_name=f"TCFD_Report_{sanitized_company_name}.pdf",
        mime="application/pdf"

    )
