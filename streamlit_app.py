"""Company AI - Streamlit Web Interface.

Interactive chat & dashboard interface for Company AI Enterprise Agent.
Communicates directly with the FastAPI backend.
"""

import os
import requests
import streamlit as st

# Configuration
API_BASE_URL = os.getenv("COMPANY_AI_API_URL", "https://company-ai-hha5.onrender.com")

st.set_page_config(
    page_title="Company AI - Enterprise Agent",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for modern enterprise look
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.0rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .source-box {
        background-color: #F3F4F6;
        border-left: 4px solid #3B82F6;
        padding: 0.8rem;
        border-radius: 4px;
        margin-top: 0.5rem;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    if "token" not in st.session_state:
        st.session_state.token = None
    if "user_info" not in st.session_state:
        st.session_state.user_info = None
    if "messages" not in st.session_state:
        st.session_state.messages = []


init_session_state()


def login(username, password):
    try:
        response = requests.post(
            f"{API_BASE_URL}/auth/login",
            json={"email": username, "password": password},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            st.session_state.token = data.get("access_token")
            st.session_state.user_info = data.get("user") or {"email": username}
            st.success("Signed in successfully!")
            st.rerun()
        else:
            err = response.json().get("detail", "Authentication failed.")
            st.error(f"Login error: {err}")
    except Exception as e:
        st.error(f"Failed to connect to backend at {API_BASE_URL}: {e}")


def logout():
    st.session_state.token = None
    st.session_state.user_info = None
    st.session_state.messages = []
    st.rerun()


# Sidebar: Authentication & Navigation
with st.sidebar:
    st.image("https://img.icons8.com/clouds/200/company.png", width=100)
    st.title("Company AI Portal")

    if not st.session_state.token:
        st.subheader("Employee Sign In")
        login_email = st.text_input("Corporate Email", placeholder="user@company.com")
        login_password = st.text_input("Password", type="password")
        if st.button("Sign In", use_container_width=True, type="primary"):
            if login_email and login_password:
                login(login_email, login_password)
            else:
                st.warning("Please provide email and password.")
    else:
        user = st.session_state.user_info
        st.success(f"Signed in as:\n**{user.get('email', 'Employee')}**")
        if user.get("full_name"):
            st.write(f"Name: {user['full_name']}")
        if user.get("department"):
            st.write(f"Dept: {user['department']}")

        st.divider()
        if st.button("Sign Out", use_container_width=True):
            logout()

        st.divider()
        st.caption("Backend Status")
        try:
            health = requests.get(f"{API_BASE_URL}/docs", timeout=3)
            if health.status_code == 200:
                st.markdown("🟢 **API Online** (`localhost:8000`)")
        except Exception:
            st.markdown("🔴 **API Disconnected**")

# Main Interface
st.markdown('<div class="main-header">🏢 Company AI Enterprise Assistant</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Ask questions regarding company policies, your projects, assigned tasks, or team performance.</div>',
    unsafe_allow_html=True,
)

if not st.session_state.token:
    st.info("👈 Please sign in using your employee credentials in the left sidebar to access chat and records.")

    st.markdown("### Sample capabilities when signed in:")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**📄 Policy Inquiries (RAG)**")
        st.caption("Ask about travel reimbursements, leave policies, remote work, benefits, and equipment allowances.")
    with col2:
        st.markdown("**📊 Structured Data Access**")
        st.caption("Check project status, team allocations, overdue tasks, or department performance metrics.")
    with col3:
        st.markdown("**🔒 Permission Enforcement**")
        st.caption("Role-based access control automatically protects confidential employee and project data.")
else:
    # Display Chat History
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sources"):
                with st.expander("📑 Referenced Sources & Policies"):
                    for src in message["sources"]:
                        st.markdown(f"- **{src.get('title', 'Document')}** (Chunk {src.get('chunk_id', 'N/A')})")

    # Chat Input
    if user_prompt := st.chat_input("Ask a question (e.g., 'What is our travel booking policy?' or 'List my overdue tasks')..."):
        # Display user message
        st.session_state.messages.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)

        # Query backend
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            message_placeholder.markdown("⏳ *Thinking...*")

            headers = {
                "Authorization": f"Bearer {st.session_state.token}",
                "Content-Type": "application/json",
            }
            try:
                response = requests.post(
                    f"{API_BASE_URL}/chat/ask",
                    headers=headers,
                    json={"message": user_prompt},
                    timeout=60,
                )
                if response.status_code == 200:
                    resp_data = response.json()
                    answer = resp_data.get("answer", "No answer provided.")
                    sources = resp_data.get("sources", [])
                    message_placeholder.markdown(answer)

                    if sources:
                        with st.expander("📑 Referenced Sources & Policies"):
                            for src in sources:
                                st.markdown(f"- **{src.get('title', 'Document')}**")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "sources": sources,
                    })
                elif response.status_code == 401:
                    message_placeholder.error("Your session has expired. Please log in again.")
                    logout()
                else:
                    detail = response.json().get("detail", response.text)
                    message_placeholder.error(f"Error ({response.status_code}): {detail}")
            except Exception as e:
                message_placeholder.error(f"Failed to communicate with AI Agent API: {e}")

