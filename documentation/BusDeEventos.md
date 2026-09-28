# El bus de eventos, explicado

> Documento de apoyo para el equipo. Complementa el "Contrato de eventos y
> definición de tools" — ahí está la forma exacta de cada evento; aquí está
> el **por qué** existe el bus y **cómo** funciona por dentro.

## 1. El problema que resuelve

El loop del agente (Python local) hace una sola cosa: habla con el proxy,
decide qué tool ejecutar, y ejecuta tools. Pero varias partes de la app
necesitan enterarse de lo que va pasando en ese ciclo:

- la mascota, para saber qué animación mostrar (idle, pensando, celebrando...)
- el motor de XP, para sumar puntos cuando termina una tarea
- la memoria, para guardar preferencias detectadas
- la UI de permisos, para mostrar el cuadro de "¿permitir esto?"
- los logs

**Sin bus**, `agent_loop.py` tendría que conocer e importar cada uno de esos
módulos y llamarlos a mano:

```python
resultado = execute_tool(...)
mascota.reaccionar("tool_result", resultado)
memoria.guardar_si_aplica(resultado)
xp_motor.sumar_xp(...)
logs.escribir(resultado)
```

Esto acopla todo: si Liz agrega una animación nueva, tiene que editar tu
`agent_loop.py`. Si el código de XP tiene un bug, puede tumbar el loop del
agente completo. Y para probar el loop solo, tendrías que simular los otros
cuatro módulos aunque no te importen para esa prueba.

**Con bus**, el loop solo hace `bus.publish(evento)` y no le importa quién
está escuchando ni cuántos son. Cada módulo se conecta por su cuenta, en su
propio archivo, sin tocar el loop.

## 2. Qué es el bus, literalmente

El bus **no es nada mágico ni de red**. Es un diccionario donde la llave es
el tipo de evento y el valor es una lista de funciones:

```python
class EventBus:
    def __init__(self):
        self._handlers: dict[EventType | None, list[Handler]] = defaultdict(list)
```

Después de que varios módulos se suscriben, ese diccionario se ve así, en
memoria, de verdad:

```python
{
    EventType.TASK_STARTED:       [mascota.reaccionar],
    EventType.DONE:                [mascota.reaccionar, xp_motor.sumar_puntos],
    EventType.PERMISSION_REQUEST: [mascota.reaccionar, ui_permisos.mostrar_prompt],
}
```

Nada más. Una lista de funciones por cada tipo de evento.

## 3. `subscribe` — apuntarse a la lista

```python
def subscribe(self, handler, event_types=None):
    if event_types is None:
        self._handlers[None].append(handler)
    else:
        for t in event_types:
            self._handlers[t].append(handler)
```

`subscribe` solo hace `.append()` a la lista correspondiente. Es como
apuntarte en una lista de WhatsApp: *"avísenme cuando pase X"*. No ejecuta
nada en el momento — solo queda anotado para el futuro.

```python
bus.subscribe(mascota.reaccionar, [EventType.TASK_STARTED, EventType.DONE])
bus.subscribe(xp_motor.sumar_puntos, [EventType.DONE])
```

`event_types=None` (sin pasar el segundo argumento) significa *"avísenme de
absolutamente todo"* — así es como funciona el `print_event` del demo, que
usamos como log temporal mientras no existen mascota/XP/memoria reales.

## 4. `publish` — avisarle a la lista

```python
def publish(self, envelope: EventEnvelope) -> None:
    for handler in self._handlers.get(envelope.type, []):
        handler(envelope)
    for handler in self._handlers.get(None, []):
        handler(envelope)
```

Traducido: *"busca en el diccionario quién se apuntó para este tipo de
evento, y llama a cada una de esas funciones, pasándole el evento."*

Si el loop hace:

```python
bus.publish(evento_done)   # evento_done.type == EventType.DONE
```

Lo que pasa por dentro, desenrollado a mano, es exactamente esto:

```python
mascota.reaccionar(evento_done)
xp_motor.sumar_puntos(evento_done)
```

**Es importante notar:** el resultado final (que se llamen esas dos
funciones) es el mismo que si el loop las llamara directo. **La diferencia
no es qué se ejecuta — es quién escribió la línea que las conecta.** Sin
bus, esas líneas las escribes tú a mano dentro del loop. Con bus, las
"escribe" automáticamente el `for` de `publish`, usando una lista que cada
dueño de módulo llenó por su cuenta con `subscribe`, sin tocar tu archivo.

## 5. Recorrido completo con un usuario real

Ana abre la app. Su mascota (un gato) está en "idle". Escribe:
**"arregla el bug en login.py"**.

### Momento 0 — arranque de la app (una sola vez)

Cada módulo se suscribe al bus al iniciar la app:

```python
bus = EventBus()

bus.subscribe(mascota.reaccionar, [EventType.TASK_STARTED, EventType.TOOL_USE,
                                     EventType.PERMISSION_REQUEST, EventType.DONE])
bus.subscribe(xp_motor.sumar_puntos, [EventType.DONE])
bus.subscribe(memoria.guardar_si_aplica, [EventType.MEMORY_SAVED])
bus.subscribe(ui_permisos.mostrar_prompt, [EventType.PERMISSION_REQUEST])
```

Nada ha pasado todavía. Solo quedó anotado quién quiere saber de qué.

### Momento 1 — Ana escribe la orden

**Aquí es clave no confundirse:** Electron **no manda ningún evento del
bus**. Electron solo manda el texto plano de Ana.

| Quién | Qué manda | ¿Es un evento del contrato? |
|---|---|---|
| Electron → Python (WebSocket) | `{"user_message": "arregla el bug en login.py"}` | ❌ No, es solo un mensaje |
| Python (`agent_loop.py`) → bus interno | `EventEnvelope(type=TASK_STARTED, ...)` | ✅ Sí |

El recorrido real:

1. Ana escribe y da enter → el renderer de Electron llama
   `window.electronAPI.enviarOrden(texto)` (IPC/contextBridge).
2. El main process de Electron reenvía ese texto por WebSocket al backend
   Python local. Sigue siendo solo texto, ningún tipo de evento.
3. Del lado de Python, el servidor FastAPI+WebSocket recibe ese mensaje y
   llama a `agent_loop.run(user_message=texto, ...)`.
4. **Recién adentro de `agent_loop.run()` nace el evento**, porque tu propio
   código lo dice explícitamente:

   ```python
   emit(EventType.TASK_STARTED, TaskStartedData(user_message=user_message))
   ```

   El tipo `TASK_STARTED` está escrito a mano en `agent_loop.py`. Nadie te
   lo mandó desde Electron — el loop decide "esto que me llegó es el inicio
   de una tarea" y publica el evento correspondiente.

5. `bus.publish(...)` recorre la lista de suscriptores de `TASK_STARTED` y
   llama a `mascota.reaccionar(evento)`.

➡️ **Lo que ve Ana:** el gato pasa de "idle" a "pensando". Eso pasó porque
`mascota.reaccionar` recibió el evento, no porque Electron le haya dicho
directamente a la mascota qué hacer.

### Momento 2 — GPT pide leer el archivo (riesgo verde)

El loop publica `TOOL_USE` con `tool_name="read_file"`, `risk_level="verde"`.

➡️ La mascota (suscrita a `TOOL_USE`) cambia su animación a "leyendo
código". Como es verde, no hay pausa de permiso — nadie más reacciona a
este evento en este caso.

### Momento 3 — GPT pide editar el archivo (riesgo amarillo)

El loop publica `PERMISSION_REQUEST`. Este evento tiene **dos**
suscriptores que no se conocen entre sí:

```python
bus.subscribe(mascota.reaccionar, [..., EventType.PERMISSION_REQUEST])
bus.subscribe(ui_permisos.mostrar_prompt, [EventType.PERMISSION_REQUEST])
```

➡️ **Lo que ve Ana:** el gato pone cara de "¿puedo?" **y al mismo tiempo**
aparece el cuadro de diálogo "¿Permitir editar login.py? [Permitir]
[Cancelar]", dibujado por un módulo totalmente distinto. Ninguno de los dos
módulos se llamó entre sí — ambos solo escuchan el mismo evento.

Ana da clic en "Permitir" → se publica `PERMISSION_RESPONSE` → el loop
ejecuta `edit_file` → se publica `TOOL_RESULT`.

### Momento 4 — la tarea termina

El loop publica `DONE`. Tiene dos suscriptores:

```python
bus.subscribe(mascota.reaccionar, [..., EventType.DONE])
bus.subscribe(xp_motor.sumar_puntos, [EventType.DONE])
```

➡️ **Lo que ve Ana:** el gato celebra (animación de la mascota) **y**
aparece "+15 XP, subiste de nivel" (módulo de XP). Dos cosas visibles a
partir de un solo `bus.publish()` en el loop.

## 6. Resumen para quien construya un módulo nuevo

Si vas a construir algo que reacciona a eventos del agente (mascota, XP,
memoria, logs, dashboard, lo que sea):

1. **No** importes ni llames a `agent_loop.py` directamente.
2. Escribe una función normal que reciba un `EventEnvelope`:

   ```python
   def mi_handler(evento):
       if evento.type == EventType.DONE:
           # tu lógica aquí
           ...
   ```

3. Suscríbela una vez, al arrancar la app:

   ```python
   bus.subscribe(mi_handler, [EventType.DONE])
   ```

4. Listo. El loop nunca se entera de que existes, y tú nunca tienes que
   tocar `agent_loop.py`.

## 7. Lo que todavía falta conectar

El esqueleto actual (`agent_loop.py` + `event_bus.py`) publica eventos
internos dentro del proceso Python. Para que la mascota en Electron los vea
de verdad, falta la pieza que reenvía esos eventos por el WebSocket hacia el
renderer — típicamente un handler más, suscrito a todo (`event_types=None`),
que hace `websocket.send(evento.to_json())`. Es exactamente el mismo patrón
que usa `print_event` en el demo, solo que en vez de imprimir, manda el
JSON a Electron.