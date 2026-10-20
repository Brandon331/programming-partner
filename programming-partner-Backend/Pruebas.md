## Instrucciones a Ejecutar para Hacer Pruebas Completas del Login y Autenticacion/Validacion del Token

## Inicia el Backend
cd "C:\Users\user\OneDrive\Documents\GitHub\programming-partner" 
*Esta direccion se cambia a donde tengas la ubicacion del proyecto en tu entorno local*

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python api_server.py

## Abre una segunda ventana en tu terminal (PowerShell) y comprueba que el servidor responda
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health

## Registra una cuenta local de pruebas
$email = "frontend.qa@example.test"
$password = "FrontQA-Local-2026!"
$body = @{ email = $email; password = $password } | ConvertTo-Json

Invoke-RestMethod `
  http://127.0.0.1:8000/api/v1/auth/register `
  -Method Post `
  -ContentType "application/json" `
  -Body $body

## Inicia sesion y guarda el access_token
$loginBody = @{ email = $email; password = $password } | ConvertTo-Json

$session = Invoke-RestMethod `
  http://127.0.0.1:8000/api/v1/auth/login `
  -Method Post `
  -ContentType "application/json" `
  -Body $loginBody

$session
$accessToken = $session.access_token

*La respuesta debe incluir access_token, token_type con valor Bearer, y el usuario. El campo token también aparece temporalmente como alias compatible.*

## Prueba una ruta protegida con el token para validar su funcionamiento
$headers = @{ Authorization = "Bearer $accessToken" }

Invoke-RestMethod `
  http://127.0.0.1:8000/api/v1/auth/me `
  -Headers $headers

*Debe devolver el usuario. Si llamas a /api/v1/auth/me sin $headers, recibirás unauthorized; en esta prueba eso es lo esperado.*

## Prueba la autenticacion del WebSocket 
$env:ACCESS_TOKEN = $accessToken

@'
import asyncio, json, os, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8001/ws") as ws:
        await ws.send(json.dumps({
            "type": "authenticate",
            "access_token": os.environ["ACCESS_TOKEN"]
        }))
        print(await ws.recv())

asyncio.run(main())
'@ | python -

*Debe responder con session_ready. Si el token no es válido, la conexión se rechazará; reinicia sesión para obtener uno nuevo.*

## Prueba una tarea sencilla de creacion de un proyecto para verificar un correcto funcionamiento del login
    $projectBody = @{ name = "Prueba frontend"; path = "prueba-frontend" } | ConvertTo-Json

    $projectResponse = Invoke-RestMethod `
    http://127.0.0.1:8000/api/v1/projects `
    -Method Post `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $projectBody

    $projectId = $projectResponse.project.id
    $projectId

## Valida que se ejecute una isnstruccion sobre el proyecto que creaste
$env:ACCESS_TOKEN = $accessToken
$env:PROJECT_ID = "$projectId"

@'
import asyncio, json, os, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8001/ws") as ws:
        await ws.send(json.dumps({
            "type": "authenticate",
            "access_token": os.environ["ACCESS_TOKEN"]
        }))
        print("Autenticación:", await ws.recv())

        await ws.send(json.dumps({
            "type": "start_task",
            "project_id": int(os.environ["PROJECT_ID"]),
            "message": "Resume este proyecto"
        }))

        while True:
            event = json.loads(await ws.recv())
            print(json.dumps(event, ensure_ascii=False, indent=2))
            if event.get("type") == "done":
                break

asyncio.run(main())
'@ | python -

*Como todavía no está conectado el proxy de IA, la tarea debería terminar con el mensaje local indicando que el backend está conectado y que falta configurar PROXY_URL. Eso es lo esperado en este punto. Sin el proxy, el agente no solicitará herramientas, así que todavía no podrás provocar un permission_request real ni probar la aprobación de permisos de punta a punta.*

## Comando para invalidad tokens y sesiones previas
@'
import secrets, sqlite3
db = sqlite3.connect("data/assistant.db")
db.execute(
    "UPDATE app_settings SET value=? WHERE key='session_secret'",
    (secrets.token_hex(32),)
)
db.commit()
db.close()
print("Sesiones anteriores invalidadas.")
'@ | python -

## Comando para reiniciar por completo los datos de prueba (invalida tokens y elimina proyecto de prueba creados)
@'
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3

db_path = Path("data/assistant.db").resolve()

if not db_path.is_file():
    raise SystemExit(f"No encontré la base de datos: {db_path}. No se borró nada.")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
backup_path = db_path.with_name(f"assistant-backup-{stamp}.db")

db = sqlite3.connect(db_path)
db.execute("PRAGMA foreign_keys = ON")

tables = {row[0] for row in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
)}
required = {"users", "projects", "app_settings"}
if not required.issubset(tables):
    db.close()
    raise SystemExit("La base no tiene el esquema esperado. No se borró nada.")

# Guarda una copia antes de hacer cambios.
backup = sqlite3.connect(backup_path)
db.backup(backup)
backup.close()

users_before = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
projects_before = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

try:
    db.execute("BEGIN IMMEDIATE")
    db.execute("DELETE FROM projects")
    db.execute("DELETE FROM users")
    db.execute(
        "UPDATE app_settings SET value=? WHERE key='session_secret'",
        (secrets.token_hex(32),)
    )
    db.commit()
except Exception:
    db.rollback()
    db.close()
    raise

db.close()
print(f"Cuentas eliminadas: {users_before}")
print(f"Proyectos eliminados: {projects_before}")
print("Tokens anteriores invalidados.")
print(f"Copia de seguridad: {backup_path}")
'@ | python -

*El bloque no borra las carpetas ni los archivos de workspaces; los registros de proyectos son referencias a esas carpetas, y podrían contener código que quieras conservar. Después, vuelve a iniciar el backend con python api_server.py, registra una cuenta nueva y crea un proyecto de prueba.*