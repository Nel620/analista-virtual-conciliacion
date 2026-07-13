"""
asistente.py
-------------
Orquestador del Analista Virtual: busca en la base de conocimiento
(PostgreSQL + pgvector) y le pide a Ollama (llama3) que redacte una
respuesta en lenguaje natural usando solo esa información.

Uso:
    python3 asistente.py "¿por qué es importante la conciliación bancaria?"
"""

import os
import sys

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/embed")
OLLAMA_CHAT_URL = os.environ.get("OLLAMA_CHAT_URL", "http://localhost:11434/api/chat")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "llama3")

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", 5432))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

SYSTEM_PROMPT = (
    "Eres un Analista Virtual experto en conciliación bancaria. "
    "Responde en español, de forma clara, completa y profesional, como le explicarías "
    "a un colega analista que está aprendiendo el proceso. "
    "Usa la información del CONTEXTO como base, pero puedes redactarla de forma fluida "
    "y natural, no solo copiarla. Si el contexto no cubre completamente la pregunta, dilo."
)


def generar_embedding(textos):
    resp = requests.post(OLLAMA_URL, json={"model": OLLAMA_EMBED_MODEL, "input": textos}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def buscar(pregunta, k=3):
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD)
    q_embedding = generar_embedding([pregunta])[0]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT doc_id, texto, 1 - (embedding <=> %s::vector) AS similitud
            FROM knowledge_base
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """, (q_embedding, q_embedding, k))
        resultados = cur.fetchall()
    conn.close()
    return resultados


def redactar_respuesta(pregunta, contexto):
    mensaje_usuario = f"CONTEXTO:\n{contexto}\n\nPREGUNTA:\n{pregunta}"

    resp = requests.post(
        OLLAMA_CHAT_URL,
        json={
            "model": OLLAMA_CHAT_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": mensaje_usuario},
            ],
            "stream": False,
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def preguntar(pregunta, k=3):
    resultados = buscar(pregunta, k=k)
    if not resultados:
        return "No encontré información relacionada en la base de conocimiento."

    contexto = "\n\n".join(r[1] for r in resultados)
    return redactar_respuesta(pregunta, contexto)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Uso: python3 asistente.py "tu pregunta aquí"')

    if not PG_PASSWORD:
        raise SystemExit("Falta definir PG_PASSWORD (usa un archivo .env, ver .env.example)")

    pregunta = sys.argv[1]
    print(preguntar(pregunta))
