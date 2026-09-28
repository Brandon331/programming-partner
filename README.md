# Esqueleto del loop del agente — semana 1-2

Implementa el ciclo descrito en `planeacion_mvp` ("El ciclo del agente") y los
contratos de `contrato_eventos_tools`: eventos (Pydantic) + tools
(`read_file`, `edit_file`, `run_command`), hablando con un proxy en vez de
directo a OpenAI.

## Estructura

```
agent_skeleton/
├── events.py          # Contrato de eventos (envelope + payload por tipo)
├── tools.py            # Contrato + ejecución de tools, sandboxing de rutas
├── permission_gate.py  # Confirmar/no confirmar (mínimo; niveles finos en sem. 3-4)
├── event_bus.py         # Bus local in-process, sin red
├── proxy_client.py      # Único módulo que sabe hablar con el proxy (SDK openai)
├── agent_loop.py        # Orquesta todo lo anterior
└── demo/
    ├── mock_proxy.py    # Proxy falso con guion, para probar sin red
    └── run_demo.py      # Corre el loop de punta a punta
```

## Cómo correr la demo (sin proxy real)

```bash
pip install pydantic
python demo/run_demo.py
```

Esto crea un proyecto temporal con un `main.py`, y simula que GPT pide leer,
editar y correr el archivo, emitiendo cada evento del bus por consola.

## Cómo conectarlo al proxy real

Cuando Rodrigo tenga el endpoint `/v1/chat` mínimo:

```python
from proxy_client import OpenAISDKProxyClient
from event_bus import EventBus
from permission_gate import PermissionGate
from agent_loop import AgentLoop

proxy = OpenAISDKProxyClient(
    base_url="https://tu-proxy.example.com",
    session_token=token_de_sesion_del_usuario,  # NUNCA la API key de OpenAI
)
loop = AgentLoop(proxy, EventBus(), PermissionGate(confirm_callback=preguntar_a_la_mascota))
loop.run(session_id="sess_1", user_message="...", project_root=Path("/ruta/al/proyecto"))
```

Requiere `pip install openai` (solo para esta implementación real del cliente;
el resto del paquete no depende de esa librería).

## Qué falta a propósito (fuera del esqueleto semana 1-2)

- `list_files`, `search`, `run_tests`, `git` como tools (semana 3-4). Se
  agregan siguiendo el mismo patrón de `tools.py`: schema + risk level +
  implementación.
- Niveles finos del permission gate cruzados con "modo de trabajo".
- Streaming real (SSE) del proxy — `ProxyClient.chat` hoy devuelve la
  respuesta completa; cuando se agregue streaming, se puede emitir
  `agent_thinking` por cada delta antes de que llegue el `tool_use` o texto
  final, sin cambiar la interfaz que usa `agent_loop.py`.
- Persistencia de memoria/XP (SQLite) — se conecta suscribiéndose al
  `EventBus`, no modificando el loop.
- El servidor FastAPI + WebSocket local que expone este loop a Electron.

## Por qué está separado así

- `events.py` y `tools.py` son **contratos**: si cambian, cambian ahí y solo
  ahí. El resto del equipo (mascota, XP, memoria) puede construir contra
  estos esquemas sin esperar a que el loop esté terminado.
- `proxy_client.py` es el único lugar que sabe que existe un proxy/HTTP —
  coincide con la nota de la planeación de aislar esa llamada en un módulo.
- `agent_loop.py` no importa `openai` ni sabe de Electron: por eso se puede
  probar con `mock_proxy.py` sin infraestructura real.
