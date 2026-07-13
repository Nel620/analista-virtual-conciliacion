"""
cargar_conocimiento.py
------------------------
Carga un archivo CSV de preguntas y respuestas conceptuales (base de
conocimiento) en PostgreSQL + pgvector, generando los embeddings con
Ollama (modelo nomic-embed-text, corriendo localmente).

Configuración por variables de entorno (ver .env.example):
    OLLAMA_URL, OLLAMA_EMBED_MODEL,
    PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

Uso:
    python3 cargar_conocimiento.py --input knowledge_base/knowledge_qa.csv
"""

import argparse
import csv
import os

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/embed")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", 5432))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

TABLA_CONOCIMIENTO = "knowledge_base"


def cargar_qa(path_csv):
    with open(path_csv, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def construir_texto(fila):
    return f"Categoría: {fila['categoria']}\nPregunta: {fila['pregunta']}\nRespuesta: {fila['respuesta']}"


def generar_embedding(textos):
    resp = requests.post(OLLAMA_URL, json={"model": OLLAMA_EMBED_MODEL, "input": textos}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def crear_tabla(conn, dim):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLA_CONOCIMIENTO} (
                doc_id     TEXT PRIMARY KEY,
                categoria  TEXT,
                pregunta   TEXT NOT NULL,
                respuesta  TEXT NOT NULL,
                texto      TEXT NOT NULL,
                embedding  VECTOR({dim})
            );
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {TABLA_CONOCIMIENTO}_embedding_hnsw
            ON {TABLA_CONOCIMIENTO} USING hnsw (embedding vector_cosine_ops);
        """)
    conn.commit()


def indexar(path_csv, conn, tamano_lote=16):
    filas = cargar_qa(path_csv)
    print(f"{len(filas)} preguntas cargadas desde {path_csv}")

    dim = None
    with conn.cursor() as cur:
        for i in range(0, len(filas), tamano_lote):
            lote = filas[i:i + tamano_lote]
            textos = [construir_texto(f) for f in lote]
            embeddings = generar_embedding(textos)

            if dim is None:
                dim = len(embeddings[0])
                crear_tabla(conn, dim)

            registros = []
            for idx, (fila, emb, texto) in enumerate(zip(lote, embeddings, textos)):
                doc_id = f"kb::{fila['categoria']}::{i + idx}"
                registros.append((doc_id, fila["categoria"], fila["pregunta"], fila["respuesta"], texto, emb))

            psycopg2.extras.execute_values(
                cur,
                f"""INSERT INTO {TABLA_CONOCIMIENTO} (doc_id, categoria, pregunta, respuesta, texto, embedding)
                    VALUES %s ON CONFLICT (doc_id) DO UPDATE SET
                        pregunta = EXCLUDED.pregunta, respuesta = EXCLUDED.respuesta,
                        texto = EXCLUDED.texto, embedding = EXCLUDED.embedding;""",
                registros, template="(%s, %s, %s, %s, %s, %s::vector)",
            )
            conn.commit()
            print(f"  Indexados {min(i + tamano_lote, len(filas))}/{len(filas)}")

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLA_CONOCIMIENTO};")
        print(f"\nTabla '{TABLA_CONOCIMIENTO}' lista con {cur.fetchone()[0]} preguntas indexadas.")


def main():
    parser = argparse.ArgumentParser(description="Carga una base de conocimiento conceptual en PostgreSQL + pgvector.")
    parser.add_argument("--input", required=True, help="Ruta al CSV con columnas: categoria, pregunta, respuesta")
    args = parser.parse_args()

    if not PG_PASSWORD:
        raise SystemExit("Falta definir PG_PASSWORD (usa un archivo .env, ver .env.example)")

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD)
    indexar(args.input, conn)
    conn.close()


if __name__ == "__main__":
    main()
