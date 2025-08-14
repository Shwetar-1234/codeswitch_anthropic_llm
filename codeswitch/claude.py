import os
import re
import anthropic
import streamlit as st
import io
import zipfile
import logging
import difflib

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('conversion.log')
    ]
)
logger = logging.getLogger(__name__)

def extract_database_schema(sql_code, source_type):
    """
    Extracts database name and schema from SQL code for logging purposes.
    """
    database, schema = None, None
    try:
        if source_type == "MSSQL":
            use_db_pattern = re.compile(r"USE\s+\[?(?P<database>[^\]\s;]+)\]?\s*;", re.IGNORECASE)
            use_db_match = use_db_pattern.search(sql_code)
            database = use_db_match.group('database').strip() if use_db_match else None
            create_pattern = re.compile(
                r"CREATE\s+(?:PROCEDURE|TABLE)\s+(?:\[(?P<schema>[^\]\.;]+)\]|\b(?P<schema2>[^\.\s;]+)\b)\.(?:\[(?P<object>[^\]\s;]+)\]|(?P<object2>[^\s;(\[]+))\s*(?:[(\[]|$)",
                re.IGNORECASE
            )
            create_match = create_pattern.search(sql_code)
            schema = (create_match.group('schema') or create_match.group('schema2')).strip() if create_match else "dbo"

        elif source_type == "Oracle":
            schema_pattern = re.compile(
                r"CREATE\s+(?:TABLE|PROCEDURE|FUNCTION)\s+(?:\"?(?P<schema>[^\"\.;]+)\"?|\b(?P<schema2>[^\.\s;]+)\b)\.(?:\"?(?P<object>[^\"\s;]+)\"?|\b(?P<object2>[^\s;(\[]+)\b)\s*(?:[(\[]|$)",
                re.IGNORECASE
            )
            schema_match = schema_pattern.search(sql_code)
            schema = (schema_match.group('schema') or schema_match.group('schema2')).strip() if schema_match else None
            database = schema

        elif source_type == "PostgreSQL":
            search_path_pattern = re.compile(r"SET\s+search_path\s+TO\s+(?P<schema>[^,;]+)\s*[,;]", re.IGNORECASE)
            search_path_match = search_path_pattern.search(sql_code)
            schema = search_path_match.group('schema').strip() if search_path_match else None
            if not schema:
                schema_pattern = re.compile(
                    r"CREATE\s+(?:TABLE|FUNCTION|PROCEDURE)\s+(?:(?P<schema>[^\.;]+)\.)?(?P<object>[^\s;(\[]+)\s*(?:[(\[]|$)",
                    re.IGNORECASE
                )
                schema_match = schema_pattern.search(sql_code)
                schema = schema_match.group('schema').strip() if schema_match and schema_match.group('schema') else "public"
            database = None

        logger.info(f"Extracted database: {database}, schema: {schema}")
        return database, schema
    except Exception as e:
        logger.error(f"Error extracting schema: {e}")
        st.warning(f"Failed to extract schema: {e}")
        return None, None

def convert_code(sql_code, source_type, target_type, conversion_type):
    """
    Converts SQL code from source to target using Anthropic's Claude model.
    """
    client = anthropic.Anthropic(api_key=st.secrets["anthropic"]["api_key"])

    source_db = {
        "MSSQL": "Microsoft SQL Server",
        "Oracle": "Oracle",
        "PostgreSQL": "PostgreSQL"
    }.get(source_type)

    target_db = {
        "Snowflake": "Snowflake",
        "Redshift": "AWS Redshift",
        "Databricks": "Azure Databricks"
    }.get(target_type)

    user_message = f"""You are an expert SQL developer specializing in {source_db} and {target_db}. 
Your task is to convert a {source_db} {conversion_type} into a {target_db}-compatible SQL {conversion_type}. 
Use only SQL, avoiding JavaScript or other languages. Ensure the output is syntactically correct, 
functionally equivalent, and optimized for {target_db}. 
Do not include database or schema qualifiers (e.g., USE DATABASE, schema.table) in the output.

Here is the {source_db} code:
<task>
{sql_code}
</task>

Provide the converted {target_db} code in a single SQL block:
```sql
-- Converted {target_db} code
<converted_code>
```"""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=8192,
            temperature=0.1,
            messages=[{"role": "user", "content": user_message}]
        )

        response_text = response.content[0].text
        start_index = response_text.find("```sql")
        end_index = response_text.find("```", start_index + 6)

        if start_index != -1 and end_index != -1:
            sql_code_block = response_text[start_index + 6:end_index].strip()
            if any(phrase in sql_code_block.lower() for phrase in ["continue with", "incomplete", "not supported"]):
                st.warning(f"Potential incomplete {conversion_type} conversion.")
                return None
            return sql_code_block
        else:
            st.warning("Failed to extract SQL code from Claude response.")
            return None
    except Exception as e:
        st.error(f"Error invoking Anthropic model: {e}")
        return None

def process_sql_code(sql_code, source_type, target_type, conversion_type):
    try:
        database, schema = extract_database_schema(sql_code, source_type)
        cleaned_sql = re.sub(r"USE\s+\[?[^\]\s;]+\]?\s*;", "", sql_code, flags=re.IGNORECASE)
        cleaned_sql = re.sub(r"CREATE\s+(TABLE|PROCEDURE|FUNCTION)\s+(?:\[(?P<schema>[^\]\.;]+)\]|\b(?P<schema2>[^\.\s;]+)\b)\.(?:\[(?P<object>[^\]\s;]+)\]|(?P<object2>[^\s;(\[]+))\s*(?:[(\[]|$)",
                           r"CREATE \1 \2\4", cleaned_sql, flags=re.IGNORECASE)
        cleaned_sql = cleaned_sql.replace("[", "").replace("]", "")
        converted_code = convert_code(cleaned_sql, source_type, target_type, conversion_type)
        return converted_code, database, schema
    except Exception as e:
        st.error(f"Error processing SQL code: {e}")
        return None, None, None

def create_zip_file(converted_files):
    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_name, converted_code in converted_files:
                zip_file.writestr(file_name, converted_code)
        zip_buffer.seek(0)
        return zip_buffer
    except Exception as e:
        st.error(f"Error creating zip file: {e}")
        return None

def main():
    st.title("Code Switch")

    with st.sidebar:
        st.header("Conversion Options")
        source_type = st.selectbox("Source Database", ["MSSQL", "Oracle", "PostgreSQL"])
        target_type = st.selectbox("Target Database", ["Snowflake", "Redshift", "Databricks"])
        conversion_type = st.selectbox("Conversion Type", ["Stored Procedure", "DDL"])

    uploaded_files = st.file_uploader("Upload SQL Files", type=["sql"], accept_multiple_files=True)

    if st.button("Convert"):
        if not uploaded_files:
            st.warning("Please upload at least one SQL file.")
            return

        converted_files = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"Processing file {i + 1}/{len(uploaded_files)}: {uploaded_file.name}")
            sql_code = uploaded_file.read().decode("utf-8")
            converted_code, database, schema = process_sql_code(sql_code, source_type, target_type, conversion_type)
            if converted_code:
                converted_files.append((os.path.basename(uploaded_file.name), converted_code))
                with st.expander(f"Preview: {uploaded_file.name}", expanded=False):
                    st.subheader("Input SQL")
                    st.code(sql_code, language="sql")
                    st.subheader("Converted SQL")
                    st.code(converted_code, language="sql")
            progress_bar.progress((i + 1) / len(uploaded_files))
        if converted_files:
            zip_file = create_zip_file(converted_files)
            st.download_button("Download All Converted Files", data=zip_file,
                               file_name="converted_sql_files.zip", mime="application/zip")

if __name__ == "__main__":
    main()
