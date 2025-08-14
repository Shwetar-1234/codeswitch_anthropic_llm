import streamlit as st
from anthropic import Anthropic

st.title("Anthropic API Key Tester")

# Read the key from Streamlit secrets
api_key = st.secrets["anthropic"]["api_key"]

# Show what was loaded (partial for safety)
if api_key:
    st.success(f"✅ Key loaded: {api_key[:8]}...{api_key[-4:]}")
else:
    st.error("❌ No API key found in secrets! Check your [anthropic] block.")

# Try calling Anthropic API
try:
    client = Anthropic(api_key=api_key)
    models = client.models.list()
    st.write("✅ Successfully connected to Anthropic. Available models:")
    st.json(models)
except Exception as e:
    st.error(f"❌ Anthropic API call failed: {e}")
