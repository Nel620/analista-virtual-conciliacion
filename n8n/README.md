# Workflow de n8n — Telegram → Analista Virtual

Este documento describe el workflow de n8n que conecta un bot de Telegram con el pipeline de búsqueda + redacción de respuestas.

## Importar el workflow

La forma más rápida: importa directamente [`workflow.example.json`](workflow.example.json) en n8n (**Workflows → Import from File**), y luego:

1. Reemplaza `TU_SERVIDOR_OLLAMA` por la IP o dominio real de tu servidor Ollama (aparece en 2 nodos: **Generar Embedding Pregunta** y **Generar Respuesta con LLM**).
2. Vuelve a asignar las credenciales de **Telegram** y **Postgres** (el archivo no las trae, por seguridad).
3. Activa el workflow.

## Diagrama del flujo

```
Recibir mensaje  →  Generar Embedding Pregunta  →  Buscar Documentos Similares
    →  Construir Contexto RAG  →  Generar Respuesta con LLM  →  Enviar Respuesta
```

## Detalle de cada nodo

### 1. Recibir mensaje
- Tipo: **Telegram Trigger**
- Updates: `message`
- Credencial: tu bot de Telegram (token de @BotFather)

### 2. Generar Embedding Pregunta
- Tipo: **HTTP Request**
- Method: `POST`
- URL: `http://TU_SERVIDOR_OLLAMA:11434/api/embed`
- Body (JSON, modo expresión):
  ```javascript
  {{ JSON.stringify({
    model: "nomic-embed-text",
    input: [$json.message.text]
  }) }}
  ```

### 3. Buscar Documentos Similares
- Tipo: **Postgres** (Execute Query)
- Credencial: tu conexión a PostgreSQL
- Query:
  ```sql
  SELECT doc_id, texto,
         1 - (embedding <=> '{{ JSON.stringify($json.embeddings[0]) }}'::vector) AS similitud
  FROM knowledge_base
  ORDER BY embedding <=> '{{ JSON.stringify($json.embeddings[0]) }}'::vector
  LIMIT 3;
  ```

### 4. Construir Contexto RAG
- Tipo: **Code** (modo: Run Once for All Items)
  ```javascript
  const contexto = items.map(item => item.json.texto).join("\n\n");

  return [{
    json: {
      contexto: contexto,
      pregunta: $node["Recibir mensaje"].json.message.text,
      chat_id: $node["Recibir mensaje"].json.message.chat.id
    },
    pairedItem: 0
  }];
  ```

### 5. Generar Respuesta con LLM
- Tipo: **HTTP Request**
- Method: `POST`
- URL: `http://TU_SERVIDOR_OLLAMA:11434/api/chat`
- Timeout: `300000` ms (300 segundos — en un servidor sin GPU, llama3 puede tardar varios minutos)
- Body (JSON, modo expresión):
  ```javascript
  {{ JSON.stringify({
    model: "llama3",
    messages: [
      {
        role: "system",
        content: "Eres un Analista Virtual experto en conciliación bancaria. Responde en español, de forma clara y profesional. Usa el CONTEXTO como base, redactado de forma fluida y natural."
      },
      {
        role: "user",
        content: "CONTEXTO:\n" + $json.contexto + "\n\nPREGUNTA:\n" + $json.pregunta
      }
    ],
    stream: false
  }) }}
  ```

### 6. Enviar Respuesta
- Tipo: **Telegram** (Send Message)
- Chat ID: `{{ $node["Construir Contexto RAG"].json.chat_id }}`
- Text: `{{ $json.message.content }}`

## Notas importantes

- **Formatear de forma segura el contenido JSON con `JSON.stringify(...)`:**: los dos nodos `HTTP Request` arman el body a partir de texto escrito por el usuario (la pregunta de Telegram). Si se escribe el JSON literal con la variable pegada directamente, un acento, comilla o salto de línea en la pregunta rompe el formato. `JSON.stringify` lo evita siempre.
- **Tiempo de espera**: si tu servidor de Ollama no tiene GPU, las respuestas de `llama3` pueden tardar bastante (se observaron hasta ~5 minutos con contexto largo). El timeout de 300000 ms ya lo contempla.
- **Mensajes duplicados**: si notas que el bot responde dos veces al mismo mensaje, generalmente se debe a que Telegram reintenta la entrega del webhook cuando la respuesta tarda mucho. Una forma de blindarse contra esto es agregar un control de deduplicación por `update_id` antes del primer nodo de procesamiento (no incluido en esta versión del workflow).
