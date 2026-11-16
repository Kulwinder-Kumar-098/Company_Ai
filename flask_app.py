"""Enterprise AI — Flask Frontend Application.

Provides a clean, standalone, responsive web frontend without third-party headers,
watermarks, or external profile links.
"""

import os
from flask import Flask, render_template, jsonify

app = Flask(__name__, template_folder="templates", static_folder="static")

# Backend API Configuration
API_BASE_URL = os.getenv("COMPANY_AI_API_URL", "https://company-ai-hha5.onrender.com")


@app.route("/")
def index():
    """Render the Enterprise AI luxury dark frontend."""
    return render_template("index.html", api_base_url=API_BASE_URL)


@app.route("/health")
def health():
    """Healthcheck endpoint for monitoring."""
    return jsonify({
        "status": "ok",
        "service": "enterprise-ai-frontend",
        "backend_url": API_BASE_URL,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"Enterprise AI Frontend running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)

