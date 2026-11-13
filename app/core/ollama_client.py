"""Factory for Ollama clients configured with the application endpoint."""

import ollama

from app.core.config import settings


def create_ollama_client(*, timeout: float) -> ollama.Client:
    return ollama.Client(host=settings.ollama_host, timeout=timeout)
