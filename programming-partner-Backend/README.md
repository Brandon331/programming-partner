# Programming Partner — backend local

Backend inicial para la app de escritorio. Conserva el loop del agente, el bus de eventos, el proxy y la demo existentes; agrega API HTTP versionada, autenticación local, proyectos SQLite, ejecución de tareas en segundo plano y transmisión de eventos por SSE o WebSocket. La API y el agente no dependen de Electron ni de la mascota.

El servidor local funciona sin proveedor de IA: en ese modo devuelve una respuesta de conexión para verificar la integración. GitHub es de solo lectura en este incremento.

## Estructura del código

- `api/`: servidor HTTP/SSE y WebSocket (`api/server.py`, `api/websocket_server.py`). `api_server.py` conserva un punto de entrada sencillo.
- `domain/`: errores del dominio y reglas compartidas.
- `infrastructure/`: SQLite, autenticación y adaptador de GitHub.
- `services/`: ejecución de tareas y coordinación de aprobaciones.
- `agent_loop.py`, `events.py`, `event_bus.py` y `tools.py`: loop original, contratos y herramientas del agente.
- `demo/` y `documentation/`: demo con proxy simulado y guía del bus de eventos.

## Requisitos e inicio

Python 3.10 o posterior. Desde esta carpeta, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python api_server.py
```

La API escucha en `http://127.0.0.1:8000` y el WebSocket en `ws://127.0.0.1:8001/ws`. Los datos locales se guardan en `data/assistant.db`; los proyectos nuevos se crean dentro de `workspaces/`. Para parar el servidor, usa Ctrl+C.

## Contrato WebSocket para Electron

1. Registra o inicia sesión por HTTP (`POST /api/v1/auth/register` o `POST /api/v1/auth/login`) y conserva `access_token` de la respuesta. `token` se incluye temporalmente como alias compatible.
2. Abre `ws://127.0.0.1:8001/ws` y envía primero `{"type":"authenticate","access_token":"<access_token>"}`. También acepta `{"type":"authenticate","token":"<access_token>"}`. En caso de éxito, recibe `{"type":"session_ready","session_id":"..."}`.
3. Inicia una tarea enviando `{"type":"start_task","project_id":1,"message":"..."}`. Los mensajes posteriores del agente son objetos JSON con la envoltura del contrato de `events.py`: `type`, `session_id`, `task_id`, `timestamp`, `data` y `event_id`.
4. Si llega un evento `permission_request`, presenta el diálogo y contesta por el mismo WebSocket con `{"type":"permission_response","task_id":"<task_id>","tool_call_id":"<tool_call_id>","approved":true}`. Usa `false` para denegar. El agente publica después un evento `permission_response` con el resultado.

Los mensajes de control que envía Electron (`authenticate`, `start_task`, `permission_response`) y el acuse `session_ready` no son eventos de agente y no usan esa envoltura. Los errores posteriores a la autenticación sí se envían como evento `error`. Una conexión WebSocket admite una tarea activa a la vez. Para una misma tarea, usa WebSocket o SSE; ambos consumen la misma cola de eventos.

El puerto WebSocket se puede cambiar con `WS_HOST` y `WS_PORT` (por defecto `127.0.0.1:8001`). El login y la sesión local pasan por este backend Python; Electron entrega el token local al WebSocket. El frontend no se conecta directamente al proxy de IA.

## Probar la API

En otra ventana de PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health

# Cuenta local exclusiva para pruebas del frontend (no usar en producción).
$testEmail = "frontend.qa@example.test"
$testPassword = "FrontQA-Local-2026!"
$registerBody = @{ email = $testEmail; password = $testPassword } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method Post -ContentType "application/json" -Body $registerBody

# Iniciar sesión y validar el campo access_token.
$loginBody = @{ email = $testEmail; password = $testPassword } | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/login -Method Post -ContentType "application/json" -Body $loginBody
$session.access_token
$headers = @{ Authorization = "Bearer $($session.access_token)" }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/me -Headers $headers

$email = "dev-$([guid]::NewGuid().ToString('N').Substring(0,8))@example.com"
$body = @{ email = $email; password = "una-clave-larga-123" } | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method Post -ContentType "application/json" -Body $body
$headers = @{ Authorization = "Bearer $($session.access_token)" }

$projectBody = @{ name = "Demo local"; path = "demo-local" } | ConvertTo-Json
$project = Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects -Method Post -Headers $headers -ContentType "application/json" -Body $projectBody
$projectId = $project.project.id

$taskBody = @{ message = "Resume este proyecto" } | ConvertTo-Json
$task = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/projects/$projectId/tasks" -Method Post -Headers $headers -ContentType "application/json" -Body $taskBody
$task
```

En modo local sin modelo, la tarea termina con una respuesta de conexión. La respuesta HTTP incluye `events_url`; los clientes que elijan SSE pueden leerlo con `fetch` y el mismo encabezado Bearer. Cada evento conserva el contrato de `events.py`.

Para probar la demo del loop por separado:

```powershell
python demo/run_demo.py
```

La demo usa un proxy simulado y aprueba sus acciones de ejemplo. La API local solicita aprobación explícita para editar archivos y correr comandos: escucha `permission_request` por SSE o WebSocket y responde por el mismo transporte. En SSE, usa `POST /api/v1/tasks/{task_id}/permissions/{tool_call_id}` con `{ "approved": true }` o `{ "approved": false }`. La espera expira después de 120 segundos y entonces se deniega.

## Conectar el proxy de IA

Instala el SDK e indica la URL de tu proxy y su token de sesión antes de iniciar el servidor:

```powershell
python -m pip install "openai>=1.30"
$env:PROXY_URL = "https://tu-proxy.example.com/v1"
$env:PROXY_SESSION_TOKEN = "token-de-sesion-del-proxy"
python api_server.py
```

El proxy debe aceptar las solicitudes de chat completions y function calling que produce el SDK. La clave del proveedor de IA permanece en el proxy, no en esta aplicación local.

## Carpetas de proyectos

Por defecto, `path` es relativo a `workspaces/`; la API rechaza rutas que intenten salir de esa raíz. Para trabajar sobre una carpeta de código existente, define `WORKSPACE_ROOT` como su carpeta contenedora y envía una ruta relativa al crear el proyecto:

```powershell
$env:WORKSPACE_ROOT = "C:\repos"
# Al crear el proyecto, usa path = "mi-aplicacion".
python api_server.py
```

Las tres herramientas actuales son `read_file`, `edit_file` y `run_command`. Las rutas de archivo se confinan al workspace seleccionado. Los comandos y cambios de archivo pasan por el permiso del usuario.

## GitHub

Para habilitar la consulta de repositorios accesibles, configura un token de lectura antes de iniciar:

```powershell
$env:GITHUB_TOKEN = "ghp_reemplazar"
python api_server.py
```

Con una sesión autenticada, `GET /api/v1/github/status` muestra si el conector está configurado y `GET /api/v1/github/repositories` devuelve repositorios. El token solo vive en el entorno del proceso y no se guarda en SQLite. Crear commits y Pull Requests no está habilitado todavía.

## Configuración opcional

- `HOST`: dirección de escucha; por defecto `127.0.0.1`.
- `PORT`: puerto HTTP; por defecto `8000`.
- `WORKSPACE_ROOT`: raíz permitida para proyectos; por defecto `./workspaces`.
- `APP_DATA_DIR` o `DATABASE_PATH`: ubicación de datos de la aplicación.
- `CORS_ORIGINS`: lista separada por comas de orígenes web permitidos; por defecto puertos locales 3000 y 5173.

La primera cuenta no recibe permisos de administrador. Los errores HTTP usan `{ "error": { "code": "...", "message": "..." } }`.
