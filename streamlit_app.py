"""Company AI - Enterprise Agent Interface.

Dark luxury, high-performance interface for Company AI Enterprise Agent.
Integrates with the FastAPI backend, Railway PostgreSQL, Qdrant Cloud, and Ollama.
"""

import os
import requests
import streamlit as st

from app.clients.document_api_client import (
    DocumentAPIError,
    get_document_status,
    list_documents,
    upload_document,
)

# Configuration
API_BASE_URL = os.getenv("COMPANY_AI_API_URL", "https://company-ai-hha5.onrender.com")

st.set_page_config(
    page_title="COMPANY AI — Enterprise Autonomous Agent",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# High-end Dark Luxury Cyberpunk Styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=Space+Grotesk:wght@500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');

    /* Global Dark Theme Overrides */
    .stApp {
        background: radial-gradient(circle at 50% 12%, #1F1A12 0%, #0C0C0E 45%, #050506 100%) !important;
        color: #EDEDED;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Hide default Streamlit header bar decoration */
    header[data-testid="stHeader"] {
        background-color: transparent !important;
    }
    
    /* Typography */
    .hero-badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: rgba(244, 238, 221, 0.08);
        border: 1px solid rgba(244, 238, 221, 0.22);
        color: #F4EEDD;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        padding: 6px 16px;
        border-radius: 9999px;
        margin-bottom: 1.2rem;
    }
    
    .hero-title {
        font-family: 'Syne', sans-serif;
        font-weight: 800;
        font-size: 4.0rem;
        line-height: 0.95;
        letter-spacing: -0.02em;
        text-transform: uppercase;
        color: #F4EEDD;
        margin-bottom: 1.2rem;
        text-shadow: 0 4px 30px rgba(0, 0, 0, 0.8);
    }
    
    .hero-subtitle {
        font-size: 1.05rem;
        font-weight: 500;
        color: #A3A3A8;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        max-width: 640px;
        line-height: 1.5;
        margin-bottom: 2.0rem;
    }

    .section-badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.15);
        color: #CCCCCC;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        padding: 5px 14px;
        border-radius: 9999px;
        margin-bottom: 1.0rem;
    }

    .section-title {
        font-family: 'Syne', sans-serif;
        font-weight: 800;
        font-size: 3.0rem;
        line-height: 1.0;
        letter-spacing: -0.01em;
        text-transform: uppercase;
        color: #F4EEDD;
        margin-bottom: 1.2rem;
    }

    .section-desc {
        font-size: 1.05rem;
        color: #BFBFBF;
        line-height: 1.7;
        max-width: 820px;
        margin-bottom: 2.5rem;
    }

    /* Glassmorphism Cards */
    .glass-card {
        background: rgba(18, 18, 22, 0.75);
        border: 1px solid rgba(244, 238, 221, 0.12);
        backdrop-filter: blur(18px);
        -webkit-backdrop-filter: blur(18px);
        border-radius: 18px;
        padding: 1.6rem;
        box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
        transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .glass-card:hover {
        border-color: rgba(244, 238, 221, 0.3);
        transform: translateY(-3px);
        box-shadow: 0 20px 45px rgba(244, 238, 221, 0.05);
    }

    .telemetry-card {
        background: rgba(20, 20, 24, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 1.2rem;
        backdrop-filter: blur(12px);
    }

    .feature-card-header {
        font-family: 'Syne', sans-serif;
        font-weight: 800;
        font-size: 1.35rem;
        color: #F4EEDD;
        margin-bottom: 0.6rem;
        letter-spacing: -0.01em;
    }

    .feature-card-body {
        font-size: 0.82rem;
        color: #9C9CA4;
        line-height: 1.55;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }

    .pill-thinking {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 14px;
        border-radius: 9999px;
        background: rgba(230, 180, 80, 0.12);
        border: 1px solid rgba(230, 180, 80, 0.35);
        color: #F5DEB3;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    /* Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #F4EEDD 0%, #E2DAC5 100%) !important;
        color: #0E0E11 !important;
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 700 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.6rem 1.6rem !important;
        transition: all 0.25s ease !important;
        box-shadow: 0 4px 20px rgba(244, 238, 221, 0.15) !important;
    }
    .stButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 30px rgba(244, 238, 221, 0.3) !important;
        background: #FFFFFF !important;
    }

    /* Streamlit Input & Form Elements in Dark */
    .stTextInput input, .stSelectbox [data-baseweb="select"] {
        background-color: rgba(18, 18, 22, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        color: #F4EEDD !important;
        border-radius: 8px !important;
    }
    .stTextInput input:focus {
        border-color: #E2DAC5 !important;
        box-shadow: 0 0 0 1px #E2DAC5 !important;
    }

    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 12px;
        background: rgba(14, 14, 18, 0.6);
        padding: 6px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stTabs [data-baseweb="tab"] {
        color: #9C9CA4 !important;
        font-weight: 600 !important;
        text-transform: uppercase !important;
        font-size: 0.85rem !important;
        letter-spacing: 0.05em !important;
        border-radius: 8px !important;
        padding: 8px 20px !important;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(244, 238, 221, 0.12) !important;
        color: #F4EEDD !important;
        border-bottom: 2px solid #E2DAC5 !important;
    }

    /* Chat Messages */
    .stChatMessage {
        background-color: rgba(18, 18, 22, 0.65) !important;
        border: 1px solid rgba(255, 255, 255, 0.07) !important;
        border-radius: 14px !important;
        padding: 1rem !important;
        margin-bottom: 0.8rem !important;
    }
    [data-testid="stChatMessageContent"] {
        color: #E5E5E5 !important;
        font-size: 0.95rem !important;
    }

    /* Watermark / Brand Header */
    .brand-logo {
        font-family: 'Syne', sans-serif;
        font-weight: 800;
        font-size: 1.5rem;
        letter-spacing: 0.15em;
        color: #F4EEDD;
        text-transform: uppercase;
    }
    .brand-watermark {
        font-family: 'Syne', sans-serif;
        font-weight: 800;
        font-size: 9.0vw;
        color: rgba(244, 238, 221, 0.025);
        letter-spacing: -0.02em;
        text-align: center;
        text-transform: uppercase;
        pointer-events: none;
        user-select: none;
        margin-top: -3rem;
        margin-bottom: -2rem;
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
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = "workspace"


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
            st.success("Authenticated successfully!")
            st.rerun()
        else:
            err = response.json().get("detail", "Authentication failed.")
            st.error(f"Sign-in error: {err}")
    except Exception as e:
        st.error(f"Failed to connect to backend at {API_BASE_URL}: {e}")


def logout():
    st.session_state.token = None
    st.session_state.user_info = None
    st.session_state.messages = []
    st.rerun()


def handle_document_api_error(error: DocumentAPIError) -> None:
    if error.status_code == 401:
        st.session_state.token = None
        st.session_state.user_info = None
        st.session_state.messages = []
        st.error("Your session has expired. Please sign in again.")
        st.rerun()
    if error.status_code == 403:
        st.error(
            "Your account is not authorized to upload this document or use "
            "the selected sharing scope."
        )
        return
    st.error(f"Document API error: {error}")


# Top Navigation Bar
top_col1, top_col2, top_col3 = st.columns([4, 3, 3])
with top_col1:
    st.markdown('<div class="brand-logo">COMPANY&nbsp;AI</div>', unsafe_allow_html=True)
with top_col2:
    pass
with top_col3:
    if st.session_state.token:
        user = st.session_state.user_info
        badge_name = user.get("full_name") or user.get("email", "Employee")
        role_label = user.get("role_name", user.get("role", "User")).upper()
        col_u, col_o = st.columns([3, 2])
        with col_u:
            st.markdown(f"👤 **{badge_name}** `[{role_label}]`")
        with col_o:
            if st.button("Sign Out", key="top_sign_out"):
                logout()
    else:
        st.markdown('<div style="text-align:right;"><span class="pill-thinking">● AGENT AVAILABLE NOW</span></div>', unsafe_allow_html=True)

st.markdown("<hr style='border-color: rgba(255,255,255,0.08); margin: 0.5rem 0 1.5rem 0;'>", unsafe_allow_html=True)

# Navigation Tabs
tab_assistant, tab_documents, tab_overview = st.tabs([
    "💬 AI Assistant Workspace",
    "📂 Document Knowledge Base",
    "🌐 Platform & Telemetry",
])


# ==========================================
# TAB 1: AI Assistant Workspace
# ==========================================
with tab_assistant:
    if not st.session_state.token:
        # Hero Section (Like Video) when not logged in
        hero_left, hero_right = st.columns([6, 5], gap="large")
        with hero_left:
            st.markdown('<div class="hero-badge">● AGENT AVAILABLE NOW</div>', unsafe_allow_html=True)
            st.markdown('<div class="hero-title">BUILD THE AGENT<br>OWN THE FUTURE</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="hero-subtitle">DEPLOY AI AGENTS THAT AUTOMATE TASKS, '
                'ENFORCE ROLE-BASED ACCESS CONTROL, AND GROW YOUR BUSINESS.</div>',
                unsafe_allow_html=True,
            )

            # Quick stats row
            s1, s2 = st.columns(2)
            with s1:
                st.markdown("""
                <div class="telemetry-card">
                    <div style="font-size:1.5rem; font-family:'Syne',sans-serif; color:#F4EEDD; font-weight:800; margin-bottom:4px;">12 TASKS</div>
                    <div style="font-size:0.75rem; color:#888;">Running automatically</div>
                </div>
                """, unsafe_allow_html=True)
            with s2:
                st.markdown("""
                <div class="telemetry-card">
                    <div style="font-size:0.75rem; color:#888; font-weight:600; text-transform:uppercase;">BUSINESS GROWTH</div>
                    <div style="font-size:1.5rem; font-family:'Syne',sans-serif; color:#F4EEDD; font-weight:800; margin:4px 0;">+38.4%</div>
                    <div style="font-size:0.75rem; color:#888;">Productivity gained</div>
                </div>
                """, unsafe_allow_html=True)

        with hero_right:
            with st.container(border=True):
                st.markdown("### 🔐 Employee Sign In")
                st.caption("Sign in with your corporate account to enter the secure workspace.")
                in_email = st.text_input("Corporate Email", placeholder="user@company.com", key="login_hero_email")
                in_pass = st.text_input("Password", type="password", key="login_hero_pass")
                if st.button("Launch Your Agent →", key="btn_hero_login", use_container_width=True):
                    if in_email and in_pass:
                        login(in_email, in_pass)
                    else:
                        st.warning("Please provide both email and password.")

    else:
        # Authenticated Chat Interface
        user = st.session_state.user_info
        role = user.get("role_name", user.get("role", "employee")).lower()
        dept = user.get("department", "Company")

        # Top User Profile Ribbon
        ribbon_col1, ribbon_col2 = st.columns([8, 2])
        with ribbon_col1:
            st.markdown(
                f"<span class='pill-thinking'>● ACTIVE SESSION</span> &nbsp; "
                f"**{user.get('full_name', 'Employee')}** &bull; {dept} &bull; "
                f"<span style='color:#D4AF37; font-weight:700;'>{role.upper()}</span>",
                unsafe_allow_html=True,
            )
        with ribbon_col2:
            if st.button("Clear Conversation", key="clear_chat_btn"):
                st.session_state.messages = []
                st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # Quick Prompt Chips
        st.caption("Suggested inquiries:")
        p_col1, p_col2, p_col3, p_col4 = st.columns(4)
        selected_prompt = None
        with p_col1:
            if st.button("💼 My Status & Role", use_container_width=True):
                selected_prompt = "What is my role and status in this company?"
        with p_col2:
            if st.button("📜 Company Leave Policy", use_container_width=True):
                selected_prompt = "What is the company leave policy?"
        with p_col3:
            if st.button("🚀 My Assigned Projects", use_container_width=True):
                selected_prompt = "What projects are allocated to me?"
        with p_col4:
            if st.button("👥 Employee Directory", use_container_width=True):
                selected_prompt = "Show all employees in the company"

        st.divider()

        # Render Chat History
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("sources"):
                    with st.expander("📑 Referenced Sources & Knowledge"):
                        for src in msg["sources"]:
                            st.markdown(f"- **{src.get('title', 'Document')}** (Chunk {src.get('chunk_id', 'N/A')})")

        # Chat input handling (from text bar or quick prompt button)
        user_prompt = st.chat_input("Ask a question about policies, projects, tasks, or team performance...")
        if selected_prompt:
            user_prompt = selected_prompt

        if user_prompt:
            st.session_state.messages.append({"role": "user", "content": user_prompt})
            with st.chat_message("user"):
                st.markdown(user_prompt)

            with st.chat_message("assistant"):
                placeholder = st.empty()
                placeholder.markdown("⏳ *Agent reasoning across authorized databases & policy retrieval...*")

                headers = {
                    "Authorization": f"Bearer {st.session_state.token}",
                    "Content-Type": "application/json",
                }
                try:
                    res = requests.post(
                        f"{API_BASE_URL}/chat/ask",
                        headers=headers,
                        json={"message": user_prompt},
                        timeout=90,
                    )
                    if res.status_code == 200:
                        data = res.json()
                        ans = data.get("answer", "No response returned.")
                        sources = data.get("sources", [])
                        placeholder.markdown(ans)
                        if sources:
                            with st.expander("📑 Referenced Sources & Knowledge"):
                                for src in sources:
                                    st.markdown(f"- **{src.get('title', 'Document')}**")
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": ans,
                            "sources": sources,
                        })
                    elif res.status_code == 401:
                        placeholder.error("Session expired. Please sign in again.")
                        logout()
                    else:
                        err_detail = res.json().get("detail", res.text)
                        placeholder.error(f"Error ({res.status_code}): {err_detail}")
                except Exception as ex:
                    placeholder.error(f"Failed to communicate with AI Agent API: {ex}")


# ==========================================
# TAB 2: Document Knowledge Base
# ==========================================
with tab_documents:
    if not st.session_state.token:
        st.info("Please sign in from the AI Assistant Workspace tab to manage enterprise documents.")
    else:
        st.markdown('<div class="section-badge">● KNOWLEDGE REPOSITORY</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-title">COMPANY POLICIES & RAG</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-desc">Upload company manuals, compliance guidelines, and HR handbooks. Documents are chunked, embedded, and stored in Qdrant Vector Cloud with granular RBAC enforcement.</div>', unsafe_allow_html=True)

        doc_col1, doc_col2 = st.columns([5, 5], gap="large")
        with doc_col1:
            st.markdown("### Upload Document")
            st.session_state.setdefault("doc_upload_gen", 0)
            upload_key = f"doc_upload_{st.session_state.doc_upload_gen}"
            with st.form("upload_form", clear_on_submit=False):
                up_file = st.file_uploader(
                    "Choose a PDF, DOCX, or TXT file",
                    type=["pdf", "docx", "txt"],
                    key=upload_key,
                )
                access_scope = st.selectbox(
                    "Who can access this document?",
                    ["private", "department", "company"],
                    format_func=lambda s: s.title(),
                    help="Company-wide sharing requires documents:publish authorization.",
                )
                btn_up = st.form_submit_button("Upload and process", type="primary")

            if btn_up and up_file:
                try:
                    doc = upload_document(
                        API_BASE_URL,
                        st.session_state.token,
                        filename=up_file.name,
                        content=up_file.getvalue(),
                        mime_type=up_file.type or "application/octet-stream",
                        access_scope=access_scope,
                    )
                    st.session_state.doc_upload_gen += 1
                    st.success(f"'{doc.get('original_filename', up_file.name)}' uploaded! Vector processing queued.")
                    st.rerun()
                except DocumentAPIError as err:
                    handle_document_api_error(err)

        with doc_col2:
            st.markdown("### Indexed Documents")
            try:
                doc_list = list_documents(API_BASE_URL, st.session_state.token, limit=20)
                if not doc_list:
                    st.info("No documents are available to your account scope.")
                for d in doc_list:
                    d_id = d.get("id")
                    fn = d.get("original_filename", "Document")
                    status = d.get("processing_status", "unknown")
                    with st.container(border=True):
                        st.markdown(f"📄 **{fn}**")
                        status_color = "#10B981" if status == "ready" else "#EAB308"
                        st.markdown(f"<span style='color:{status_color}; font-weight:700;'>● {status.upper()}</span> &bull; Version {d.get('version_number', 1)} &bull; Scope: {d.get('access_scope', 'private').title()}", unsafe_allow_html=True)
            except DocumentAPIError as err:
                handle_document_api_error(err)


# ==========================================
# TAB 3: Platform & Telemetry (Video Replica)
# ==========================================
with tab_overview:
    st.markdown('<div class="section-badge">● WHAT YOUR AGENT CAN DO</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">YOUR BUSINESS.<br>ON AUTOPILOT.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-desc">'
        'Give your business the freedom to move faster without adding more to your plate. '
        'AI agents handle the repetitive work, make decisions in real time, and keep your workflows '
        'moving around the clock so you can spend less time managing tasks and more time building what\'s next.'
        '</div>',
        unsafe_allow_html=True,
    )

    # 3 Feature Cards (from video)
    f1, f2, f3 = st.columns(3, gap="medium")
    with f1:
        st.markdown("""
        <div class="glass-card">
            <div style="font-size:2.0rem; margin-bottom:12px;">🔍</div>
            <div class="feature-card-header">Find what matters.</div>
            <div class="feature-card-body">
                YOUR AGENT SEARCHES THROUGH INFORMATION, ANALYZES COMPANY POLICIES, MONITORS TRENDS, AND TURNS SCATTERED DATA INTO ACTIONABLE INSIGHTS.
            </div>
        </div>
        """, unsafe_allow_html=True)

    with f2:
        st.markdown("""
        <div class="glass-card">
            <div style="font-size:2.0rem; margin-bottom:12px;">⚡</div>
            <div class="feature-card-header">Get work done.</div>
            <div class="feature-card-body">
                FROM TRACKING PROJECT DELIVERABLES TO CHECKING EMPLOYEE ALLOCATIONS, YOUR AGENT HANDLES WORKFLOWS WITHOUT WAITING FOR YOU.
            </div>
        </div>
        """, unsafe_allow_html=True)

    with f3:
        st.markdown("""
        <div class="glass-card">
            <div style="font-size:2.0rem; margin-bottom:12px;">🛡️</div>
            <div class="feature-card-header">Turn data into action.</div>
            <div class="feature-card-body">
                YOUR AGENT UNDERSTANDS CONTEXT, EVALUATES RIGID ROLE-BASED ACCESS POLICIES, AND RECOMMENDS THE NEXT BEST MOVE SECURELY.
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br><br>", unsafe_allow_html=True)

    # Live Health & Telemetry Check
    st.markdown("### 🖥️ Live Infrastructure Telemetry")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        try:
            r = requests.get(f"{API_BASE_URL}/health", timeout=3)
            stat = "ONLINE" if r.status_code == 200 else "OFFLINE"
        except Exception:
            stat = "OFFLINE"
        st.metric("FastAPI Backend", stat, "Render Cloud")

    with t2:
        st.metric("Vector Database", "Qdrant Cloud", "28 Chunks Active")

    with t3:
        st.metric("Relational Database", "PostgreSQL", "Railway Connected")

    with t4:
        st.metric("Inference Engine", "Ollama + Groq", "Llama 3.2 + GPT-OSS")

    st.markdown('<div class="brand-watermark">COMPANY AI</div>', unsafe_allow_html=True)
