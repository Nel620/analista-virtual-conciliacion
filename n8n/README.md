# Workflow de n8n — Telegram → Analista Virtual

Este documento describe cómo reconstruir el workflow de n8n que conecta un bot de Telegram con el pipeline de búsqueda + redacción de respuestas.

No se incluye el archivo `.json` exportado del workflow porque contendría credenciales y el token del bot. Sigue estos pasos para recrearlo desde cero (toma menos de 15 minutos).

## Prerrequisitos

- Un bot de Telegram creado con [@BotFather](https://t.me/BotFather), con su token.
- n8n corriendo y accesible.
- La base de conocimiento ya cargada en PostgreSQL (ver el README principal, pasos 1 a 3).

## Nodos del workflow

```
Telegram Trigger → Postgres (deduplicar) → IF → HTTP Request (embedding)
    → Postgres (buscar) → Code (armar contexto) → HTTP Request (llama3) → Telegram (responder)
```

### 1. Telegram Trigger
- Tipo de nodo: **Telegram Trigger**
- Credencial: tu bot, con el token de @BotFather
- Updates: `message`

### 2. Postgres — deduplicar mensajes (evita respuestas repetidas)

Antes de crear este nodo, ejecuta una sola vez en tu base de datos:

```sql
CREATE TABLE IF NOT EXISTS mensajes_procesados (
    update_id BIGINT PRIMARY KEY,
    procesado_en TIMESTAMP DEFAULT now()
);
```

Nodo tipo **Postgres**, modo **Execute Query**:

```sql
INSERT INTO mensajes_procesados (update_id)
VALUES ({{ $json.update_id }})
ON CONFLICT (update_id) DO NOTHING
RETURNING update_id;
```

### 3. IF — continuar solo si el mensaje es nuevo

Condición: el resultado del nodo anterior no está vacío.
- Rama **true**: continúa el flujo normal.
- Rama **false**: no conectar nada (el mensaje ya fue procesado, se ignora).

### 4. HTTP Request — generar embedding de la pregunta

- Method: `POST`
- URL: `http://TU_SERVIDOR_OLLAMA:11434/api/embed`
- Body (modo expresión, usando `JSON.stringify` para evitar errores con comillas o tildes):

```javascript
{{ JSON.stringify({
  model: "nomic-embed-text",
  input: [$json.message.text]
}) }}
```

### 5. Postgres — buscar en la base de conocimiento

```sql
SELECT doc_id, texto,
       1 - (embedding <=> '{{ JSON.stringify($json.embeddings[0]) }}'::vector) AS similitud
FROM knowledge_base
ORDER BY embedding <=> '{{ JSON.stringify($json.embeddings[0]) }}'::vector
LIMIT 3;
```

### 6. Code — armar el contexto

Modo: **Run Once for All Items**

```javascript
const contexto = items.map(item => item.json.texto).join("\n\n");

return [{
  json: {
    contexto: contexto,
    pregunta: $node["Telegram Trigger"].json.message.text,
    chat_id: $node["Telegram Trigger"].json.message.chat.id
  },
  pairedItem: 0
}];
```

### 7. HTTP Request — redactar la respuesta con llama3

- Method: `POST`
- URL: `http://TU_SERVIDOR_OLLAMA:11434/api/chat`
- Timeout: `300000` (300 segundos — en un servidor sin GPU, llama3 puede tardar varios minutos)
- Body (modo expresión):

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

### 8. Telegram — enviar la respuesta

- Resource: `Message`
- Operation: `Send Message`
- Chat ID: `{{ $node["Code"].json.chat_id }}` (ajusta el nombre del nodo si le pusiste otro)
- Text: `{{ $json.message.content }}`

## Notas de robustez

- El nodo de deduplicación (paso 2 y 3) es importante: Telegram puede reintentar la entrega de un mensaje si el servidor tarda en responder, y sin este control el bot puede responder dos veces al mismo mensaje.
- Todos los `HTTP Request` que arman el body a mano (con texto del usuario adentro) deben usar `JSON.stringify(...)`, nunca escribir el JSON literal con la variable pegada directamente — un acento, comilla o salto de línea en la pregunta del usuario rompe el formato si no se escapa correctamente.
