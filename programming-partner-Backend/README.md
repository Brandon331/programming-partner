# Programming Partner — backend local

Backend inicial para la app de escritorio. Conserva el loop del agente, el bus de eventos, el proxy y la demo existentes; agrega API HTTP versionada, autenticación local, proyectos SQLite, ejecución de tareas en segundo plano y transmisión SSE de eventos. La API y el agente no dependen de Electron ni de la mascota.

El servidor local funciona sin proveedor de IA: en ese modo devuelve una respuesta de conexión para verificar la integración. GitHub es de solo lectura en este incremento.

## Estructura del código

- `api/`: rutas y servidor HTTP/SSE (`api/server.py`). `api_server.py` conserva un punto de entrada sencillo.
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

La API escucha en `http://127.0.0.1:8000`. Los datos locales se guardan en `data/assistant.db`; los proyectos nuevos se crean dentro de `workspaces/`. Para parar el servidor, usa Ctrl+C.

## Probar la API

En otra ventana de PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health

$email = "dev-$([guid]::NewGuid().ToString('N').Substring(0,8))@example.com"
$body = @{ email = $email; password = "una-clave-larga-123" } | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method Post -ContentType "application/json" -Body $body
$headers = @{ Authorization = "Bearer $($session.token)" }

$projectBody = @{ name = "Demo local"; path = "demo-local" } | ConvertTo-Json
$project = Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects -Method Post -Headers $headers -ContentType "application/json" -Body $projectBody
$projectId = $project.project.id

$taskBody = @{ message = "Resume este proyecto" } | ConvertTo-Json
$task = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/projects/$projectId/tasks" -Method Post -Headers $headers -ContentType "application/json" -Body $taskBody
$task
```

En modo local sin modelo, la tarea termina con una respuesta de conexión. La respuesta incluye `events_url`; Electron puede consumirla con `fetch` y leer el flujo SSE usando el mismo encabezado Bearer. Cada evento conserva el contrato de `events.py`.

Para probar la demo del loop por separado:

```powershell
python demo/run_demo.py
```

La demo usa un proxy simulado y aprueba sus acciones de ejemplo. La API local solicita aprobación explícita para editar archivos y correr comandos: escucha `permission_request` en el flujo SSE y responde a `POST /api/v1/tasks/{task_id}/permissions/{tool_call_id}` con `{ "approved": true }` o `{ "approved": false }`. La espera expira después de 120 segundos y entonces se deniega.

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
