/**
 * Enterprise AI — Modern Vanilla JS Application Controller
 * Connects directly to FastAPI backend with zero external dependencies.
 */

// Dynamically use configured backend URL or current host
const API_BASE_URL = window.COMPANY_AI_API_URL || "https://company-ai-hha5.onrender.com";

// Application State
const AppState = {
    token: localStorage.getItem("enterprise_ai_token") || null,
    user: JSON.parse(localStorage.getItem("enterprise_ai_user") || "null"),
    messages: []
};

// DOM Content Loaded
document.addEventListener("DOMContentLoaded", () => {
    initNavigationTabs();
    initAuthUI();
    initChatUI();
    initDocumentUI();
});

// ==========================================
// Navigation Tabs
// ==========================================
function initNavigationTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    const tabContents = document.querySelectorAll(".tab-content");

    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.getAttribute("data-tab");

            tabBtns.forEach(b => b.classList.remove("active"));
            tabContents.forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const targetEl = document.getElementById(target);
            if (targetEl) targetEl.classList.add("active");

            // Refresh documents when switching to document tab
            if (target === "tab-documents" && AppState.token) {
                loadDocumentList();
            }
        });
    });
}

// ==========================================
// Authentication
// ==========================================
function initAuthUI() {
    const loginForm = document.getElementById("login-form");
    const signoutBtn = document.getElementById("btn-signout");
    const unauthView = document.getElementById("unauth-view");
    const authView = document.getElementById("auth-view");
    const userBadge = document.getElementById("user-badge");
    const userNameSpan = document.getElementById("user-name");
    const userRoleSpan = document.getElementById("user-role");

    function renderAuthState() {
        if (AppState.token && AppState.user) {
            if (unauthView) unauthView.style.display = "none";
            if (authView) authView.style.display = "block";
            if (userBadge) userBadge.style.display = "flex";
            if (userNameSpan) userNameSpan.textContent = AppState.user.full_name || AppState.user.email || "Employee";
            if (userRoleSpan) userRoleSpan.textContent = (AppState.user.role_name || AppState.user.role || "USER").toUpperCase();
        } else {
            if (unauthView) unauthView.style.display = "grid";
            if (authView) authView.style.display = "none";
            if (userBadge) userBadge.style.display = "none";
        }
    }

    if (loginForm) {
        loginForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const emailInput = document.getElementById("login-email");
            const passInput = document.getElementById("login-pass");
            const errorMsg = document.getElementById("login-error");
            const submitBtn = document.getElementById("btn-login-submit");

            if (!emailInput.value || !passInput.value) {
                if (errorMsg) errorMsg.textContent = "Please provide email and password.";
                return;
            }

            try {
                if (submitBtn) submitBtn.disabled = true;
                if (submitBtn) submitBtn.textContent = "Authenticating...";
                if (errorMsg) errorMsg.textContent = "";

                const res = await fetch(`${API_BASE_URL}/auth/login`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        email: emailInput.value.trim(),
                        password: passInput.value
                    })
                });

                if (res.ok) {
                    const data = await res.json();
                    AppState.token = data.access_token;
                    AppState.user = data.user || { email: emailInput.value };
                    localStorage.setItem("enterprise_ai_token", AppState.token);
                    localStorage.setItem("enterprise_ai_user", JSON.stringify(AppState.user));
                    renderAuthState();
                } else {
                    const err = await res.json();
                    if (errorMsg) errorMsg.textContent = err.detail || "Authentication failed.";
                }
            } catch (err) {
                if (errorMsg) errorMsg.textContent = "Failed to connect to backend service.";
            } finally {
                if (submitBtn) submitBtn.disabled = false;
                if (submitBtn) submitBtn.textContent = "Launch Your Agent →";
            }
        });
    }

    if (signoutBtn) {
        signoutBtn.addEventListener("click", () => {
            AppState.token = null;
            AppState.user = null;
            AppState.messages = [];
            localStorage.removeItem("enterprise_ai_token");
            localStorage.removeItem("enterprise_ai_user");
            renderAuthState();
        });
    }

    renderAuthState();
}

// ==========================================
// Chat Interface
// ==========================================
function initChatUI() {
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatMessages = document.getElementById("chat-messages");
    const clearChatBtn = document.getElementById("btn-clear-chat");
    const promptChips = document.querySelectorAll(".chip-btn");

    function appendMessage(role, content, sources = null) {
        if (!chatMessages) return;
        const bubble = document.createElement("div");
        bubble.className = `message-bubble ${role}`;
        
        let html = `<div>${escapeHtml(content).replace(/\n/g, '<br>')}</div>`;
        if (sources && sources.length > 0) {
            html += `<div class="citations-box"><strong>Referenced Knowledge:</strong><br>`;
            sources.forEach(s => {
                html += `&bull; ${escapeHtml(s.title || "Policy Document")}<br>`;
            });
            html += `</div>`;
        }
        bubble.innerHTML = html;
        chatMessages.appendChild(bubble);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function sendQuery(text) {
        if (!text || !text.trim() || !AppState.token) return;
        appendMessage("user", text);

        // Thinking placeholder
        const thinkingBubble = document.createElement("div");
        thinkingBubble.className = "message-bubble assistant";
        thinkingBubble.innerHTML = "<em>⏳ Agent reasoning across authorized databases & policy retrieval...</em>";
        chatMessages.appendChild(thinkingBubble);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        try {
            const res = await fetch(`${API_BASE_URL}/chat/ask`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${AppState.token}`
                },
                body: JSON.stringify({ message: text.trim() })
            });

            thinkingBubble.remove();

            if (res.ok) {
                const data = await res.json();
                appendMessage("assistant", data.answer || "No response returned.", data.sources);
            } else if (res.status === 401) {
                appendMessage("assistant", "Your session has expired. Please sign in again.");
                localStorage.removeItem("enterprise_ai_token");
                location.reload();
            } else {
                const err = await res.json();
                appendMessage("assistant", `Error (${res.status}): ${err.detail || "Unable to process query."}`);
            }
        } catch (err) {
            thinkingBubble.remove();
            appendMessage("assistant", "Communication failure with Enterprise AI backend.");
        }
    }

    if (chatForm && chatInput) {
        chatForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const text = chatInput.value;
            chatInput.value = "";
            sendQuery(text);
        });
    }

    promptChips.forEach(chip => {
        chip.addEventListener("click", () => {
            const prompt = chip.getAttribute("data-prompt");
            if (prompt) sendQuery(prompt);
        });
    });

    if (clearChatBtn && chatMessages) {
        clearChatBtn.addEventListener("click", () => {
            chatMessages.innerHTML = "";
            appendMessage("assistant", "Conversation cleared. How can I assist you with corporate intelligence today?");
        });
    }
}

// ==========================================
// Document Management
// ==========================================
function initDocumentUI() {
    const uploadForm = document.getElementById("document-upload-form");
    if (uploadForm) {
        uploadForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const fileInput = document.getElementById("doc-file-input");
            const scopeSelect = document.getElementById("doc-scope-select");
            const statusMsg = document.getElementById("upload-status-msg");
            const uploadBtn = document.getElementById("btn-doc-upload");

            if (!fileInput.files || fileInput.files.length === 0) {
                if (statusMsg) statusMsg.textContent = "Please choose a file before uploading.";
                return;
            }

            const formData = new FormData();
            formData.append("file", fileInput.files[0]);
            formData.append("access_scope", scopeSelect.value);

            try {
                if (uploadBtn) uploadBtn.disabled = true;
                if (statusMsg) statusMsg.textContent = "Uploading & queuing vector ingestion...";

                const res = await fetch(`${API_BASE_URL}/documents/upload`, {
                    method: "POST",
                    headers: {
                        "Authorization": `Bearer ${AppState.token}`
                    },
                    body: formData
                });

                if (res.ok) {
                    if (statusMsg) statusMsg.textContent = "Upload successful! Ingestion queued.";
                    fileInput.value = "";
                    loadDocumentList();
                } else {
                    const err = await res.json();
                    if (statusMsg) statusMsg.textContent = `Upload failed: ${err.detail || "Unauthorized"}`;
                }
            } catch (err) {
                if (statusMsg) statusMsg.textContent = "Failed to upload file.";
            } finally {
                if (uploadBtn) uploadBtn.disabled = false;
            }
        });
    }
}

async function loadDocumentList() {
    const listContainer = document.getElementById("doc-list-container");
    if (!listContainer || !AppState.token) return;

    try {
        const res = await fetch(`${API_BASE_URL}/documents?limit=20`, {
            headers: {
                "Authorization": `Bearer ${AppState.token}`
            }
        });

        if (res.ok) {
            const docs = await res.json();
            if (docs.length === 0) {
                listContainer.innerHTML = "<p style='color: #888;'>No documents available in your access scope.</p>";
                return;
            }
            let html = "";
            docs.forEach(doc => {
                const statusColor = doc.processing_status === "ready" ? "#10B981" : "#EAB308";
                html += `
                <div class="telemetry-card" style="margin-bottom: 10px;">
                    <div style="font-weight: 700; color: #F4EEDD;">📄 ${escapeHtml(doc.original_filename)}</div>
                    <div style="font-size: 0.78rem; color: #888; margin-top: 4px;">
                        <span style="color: ${statusColor}; font-weight: 700;">● ${doc.processing_status.toUpperCase()}</span>
                        &bull; Version ${doc.version_number || 1}
                        &bull; Scope: ${doc.access_scope}
                    </div>
                </div>`;
            });
            listContainer.innerHTML = html;
        }
    } catch (err) {
        listContainer.innerHTML = "<p style='color: #888;'>Unable to fetch document roster.</p>";
    }
}

// Utility: HTML Escape
function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

