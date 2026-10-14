# Together

Preguntas en vivo para la clase, al estilo Mentimeter, pero corriendo en tu computadora.
Los alumnos entran escaneando un QR o abriendo un enlace. No hace falta crear cuentas.

- **Clase online** (`--publico`): se genera una URL pública `https://…` gratuita y los
  alumnos entran desde cualquier lugar.
- **Clase presencial**: sin `--publico`, los alumnos tienen que estar en la misma red WiFi.

## Clase online (URL pública)

```bash
.venv/bin/python together.py --publico         # Windows: .venv\Scripts\python together.py --publico
```

La terminal muestra la URL para los alumnos. El botón **Copiar enlace** del presentador también
la copia, así podés pegarla en el chat de Zoom, Meet o Teams. Además, el QR de la pantalla
compartida apunta a esa URL.

La URL pública sale de un **túnel gratuito**: tu computadora sigue corriendo el programa y el
servicio reenvía las visitas. El programa prueba dos opciones, en este orden:

1. **Cloudflare Tunnel** (recomendado, más estable). No requiere cuenta, pero hay que instalar `cloudflared` una vez:
   - Windows: `winget install --id Cloudflare.cloudflared`
   - Mac: `brew install cloudflared`
   - Linux: descargá el `.deb` o el binario desde https://github.com/cloudflare/cloudflared/releases
2. **localhost.run**: no hay que instalar nada, solo usa el comando `ssh`, que ya viene en Linux,
   Mac y Windows 10/11. Es más propenso a cortes.

Para elegir uno a mano: `--publico --tunel cloudflared` o `--publico --tunel ssh`.

Para tener en cuenta:
- **La URL cambia cada vez que abrís el programa.** Pasala por el chat al empezar la clase.
- Si el túnel se corta, el programa lo reabre solo y actualiza el QR, pero la URL nueva es
  distinta. Vas a tener que volver a pasarla por el chat. Las respuestas no se pierden.
- La computadora tiene que quedar encendida y conectada durante toda la clase.
- Cualquiera que tenga la URL puede responder, pero solo vos podés pasar las diapositivas
  (el enlace del presentador tiene clave y no pasa por el túnel).

## Instalación (una sola vez)

Necesitás Python 3.8 o superior.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
```

`segno` es lo único que se instala y sirve para dibujar el QR. Sin segno el programa
igual funciona, pero solo muestra la dirección escrita.

## Uso en clase presencial

```bash
.venv/bin/python together.py                  # Windows: .venv\Scripts\python together.py
```

1. Se abre el navegador con la **vista del presentador**: proyectala y apretá `F` para pantalla completa.
2. Los alumnos escanean el QR, o escriben la dirección que aparece debajo del QR.
3. Avanzá con `→`, la barra espaciadora o `Av Pág`. También funciona con un pasador de diapositivas.
   Para volver usá `←` o `Re Pág`.
4. Cada pregunta tiene una **cuenta regresiva** (30 s por defecto). Mientras corre, la pantalla
   muestra las afirmaciones, cuántos respondieron («12 de 17 respondieron») y los nombres de
   quienes ya respondieron. Los resultados quedan ocultos para que nadie se influya.
5. Los resultados se revelan con una animación cuando **respondieron todos** (con confeti y el
   aviso «¡Respondieron todos!») o cuando **se termina el tiempo**. Después del tiempo no se
   aceptan más respuestas.
6. Atajos: `H` muestra u oculta los resultados antes de tiempo, `T` reinicia la cuenta regresiva
   y `+` agrega 15 segundos.

«Todos» son las personas conectadas en ese momento que ya escribieron su nombre.

Antes de responder, cada alumno escribe su **nombre y apellido**. Queda guardado en su
navegador y lo puede cambiar con «cambiar nombre».

Las respuestas se guardan solas en dos archivos:
- `resultados-presentacion.csv`: una fila por alumno, con su nombre y una columna por
  pregunta. Se abre con Excel o Google Sheets.
- `resultados-presentacion.json`: todos los datos, que el programa usa para retomar. Si cerrás el programa y lo
volvés a abrir, las respuestas se conservan. Para empezar de cero usá `--reiniciar`.

El enlace del presentador lleva una clave (`?k=...`) que cambia cada vez que abrís el programa.
**No lo compartas**: con ese enlace se controla la presentación.

### Opciones

| Opción | Para qué sirve |
|---|---|
| `otra.json` | Usar otro archivo de diapositivas, por ejemplo para la clase 2 |
| `--publico` | Crear una URL pública para clases online |
| `--tiempo 45` | Segundos para responder cada pregunta (`0` = sin límite) |
| `--puerto 8080` | Cambiar el puerto (por defecto 8000) |
| `--ip 192.168.0.10` | Forzar la IP que aparece en el QR, si la detección automática eligió otra red |
| `--reiniciar` | Ignorar las respuestas guardadas |
| `--clave abc` | Usar una clave fija para el presentador |

## Flujo de la Clase 1

| # | Diapositiva | Cuándo |
|---|---|---|
| 1 | Portada con QR grande | Mientras van entrando |
| 2 | «¿Hasta dónde llegaste con los temas de la clase?» (opción única) | Al empezar |
| 3 | «¿Qué tema te generó más dudas?» (opción única) | Al empezar |
| 4, 6, 8, 10 | Tema N: «¿qué sabés hoy?» (¿ya lo viste? / ¿lo entendés?) | **Antes** de explicar cada tema |
| 5, 7, 9, 11 | Tema N: «¿y ahora?» (¿lo entendés? / ¿mejoró?) | **Después** de explicarlo |
| 12 | Nube de palabras de la clase | Al cierre |

Cada afirmación muestra la **distribución** de las respuestas: barras con el % de alumnos que
eligió cada valor del 1 al 5 y una curva suavizada encima. Al pasar el mouse por una barra se
ve la cantidad exacta. En las diapositivas de «¿y ahora?», la afirmación «Entiendo…» suma la
curva de «antes de la explicación» en línea punteada gris, así se ve cuánto se corrió hacia la
derecha.

## Editar o crear presentaciones

Copiá `presentacion.json` y modificalo. Hay cuatro tipos de diapositivas: contenido, escalas
(1 a 5), nube de palabras y opciones (se elige una sola; el resultado se ve como barras
horizontales con el % y la cantidad de personas):

```json
{"id": "portada", "tipo": "contenido", "titulo": "…", "texto": "…", "mostrar_qr": true}

{"id": "t1-antes", "tipo": "escalas", "titulo": "…",
 "afirmaciones": [{"texto": "Ya lo vi"}, {"texto": "Lo entiendo"}],
 "min": 1, "max": 5, "etiqueta_min": "Nada", "etiqueta_max": "Mucho"}

{"id": "t1-despues", "tipo": "escalas", "titulo": "…",
 "afirmaciones": [{"texto": "Lo entiendo", "comparar_con": {"id": "t1-antes", "afirmacion": 1}}]}

{"id": "nube", "tipo": "nube", "titulo": "…", "max_palabras": 3}

{"id": "dudas", "tipo": "opciones", "titulo": "¿Qué tema te generó más dudas?",
 "ayuda": "Elegí uno solo.", "opciones": ["Tema 1", "Tema 2", "Tema 3"]}
```

El tiempo se configura con `"tiempo": 30` al principio del archivo, para todas las preguntas, o
dentro de una diapositiva, solo para esa. En la Clase 1, la nube de palabras tiene 60 s. La opción
`--tiempo` tiene prioridad sobre el valor general.

Cada `id` tiene que ser único. En `comparar_con`, `afirmacion` es la posición dentro de la
diapositiva referida, contando desde 0.

## Si los celulares no se conectan (clase presencial)

- **Firewall de Windows:** la primera vez, Windows pregunta si permite a Python usar la red.
  Aceptá en *redes privadas*. Si no apareció la pregunta, permití Python en
  «Firewall de Windows → Permitir una aplicación».
- **WiFi de la facultad:** muchas redes institucionales no dejan que los dispositivos se vean entre
  sí. La solución más simple es compartir internet desde tu celular (hotspot), conectar la
  computadora y los alumnos a esa red, y volver a abrir el programa.
- **IP equivocada en el QR** (por ejemplo, si tenés VPN o varias placas de red): averiguá tu IP con
  `ipconfig` (Windows) o `ip addr` (Linux) y usá `--ip`.
