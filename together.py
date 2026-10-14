#!/usr/bin/env python3
"""Together: preguntas en vivo para la clase, con QR, sin servicios externos.

Uso:
    python together.py                      # usa presentacion.json
    python together.py otra.json --puerto 8080

Los alumnos se conectan desde el celular (misma red WiFi) escaneando el QR.
"""
import argparse
import csv
import io
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import segno
except ImportError:
    segno = None

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
TIPOS = {"contenido", "escalas", "nube", "opciones"}
MAX_VOTANTES = 1000  # por diapositiva, para limitar abusos con la URL pública

# Túneles gratuitos para obtener una URL pública: (programa, comando, patrón de la URL)
TUNELES = [
    ("cloudflared",
     lambda p: ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{p}"],
     r"https://[a-z0-9-]+\.trycloudflare\.com"),
    ("ssh",
     lambda p: ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                "-o", "ExitOnForwardFailure=yes", "-R", f"80:localhost:{p}", "nokey@localhost.run"],
     r"https://[a-z0-9-]+\.lhr\.life"),
]


def ip_local():
    """IP de esta computadora en la red local (no envía paquetes)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def normalizar(palabra):
    p = re.sub(r"[^\w\s.+#-]", "", palabra.strip().lower())
    return re.sub(r"\s+", " ", p).strip()[:30]


class Estado:
    def __init__(self, presentacion, archivo_resultados, reiniciar=False, tiempo=None):
        self.lock = threading.Lock()
        self.titulo = presentacion.get("titulo", "Presentación")
        self.slides = presentacion["diapositivas"]
        # Segundos para responder: --tiempo > "tiempo" de la presentación > 30. 0 = sin límite.
        self.tiempo = tiempo if tiempo is not None else presentacion.get("tiempo", 30)
        self.indice = 0
        self._iniciar_diapositiva()
        self.archivo = archivo_resultados
        self.respuestas = {s["id"]: {} for s in self.slides}  # id -> votante -> respuesta
        self.vistos = {}  # votante -> último pedido (para contar conectados)
        self.nombres = {}  # votante -> nombre que escribió
        if self.archivo.exists() and not reiniciar:
            guardado = json.loads(self.archivo.read_text(encoding="utf-8"))
            self.nombres = guardado.get("nombres", {})
            for sid, resp in guardado.get("respuestas", {}).items():
                if sid in self.respuestas:
                    self.respuestas[sid] = resp

    @property
    def actual(self):
        return self.slides[self.indice]

    def _iniciar_diapositiva(self):
        s = self.actual
        self.inicio = time.time()
        self.duracion = 0 if s["tipo"] == "contenido" else s.get("tiempo", self.tiempo)
        self.revelado = False  # el presentador puede mostrar los resultados antes de tiempo

    def _numeracion(self):
        """(número de esta pregunta, total de preguntas), sin contar diapositivas de contenido."""
        preguntas = [i for i, x in enumerate(self.slides) if x["tipo"] != "contenido"]
        numero = sum(1 for i in preguntas if i <= self.indice)
        return numero, len(preguntas)

    def _restante(self):
        """Segundos que quedan para responder (None = sin límite)."""
        if not self.duracion:
            return None
        return max(0.0, self.inicio + self.duracion - time.time())

    def _participacion(self):
        """(respondieron, esperados, nombres de quienes respondieron) en la diapositiva actual."""
        ahora = time.time()
        respondieron = self.respuestas[self.actual["id"]]
        presentes = {v for v, t in self.vistos.items() if ahora - t < 8 and v in self.nombres}
        esperados = presentes | set(respondieron)
        nombres = [self.nombres.get(v, "") for v in respondieron]
        return len(respondieron), len(esperados), nombres

    def _guardar(self):
        datos = {
            "titulo": self.titulo,
            "guardado": datetime.now().isoformat(timespec="seconds"),
            "resumen": {s["id"]: self._resultados(s) for s in self.slides if s["tipo"] != "contenido"},
            "nombres": self.nombres,
            "respuestas": self.respuestas,
        }
        tmp = self.archivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.archivo)
        self._guardar_csv()

    def _guardar_csv(self):
        """Una fila por alumno, una columna por afirmación (para abrir en Excel o Sheets)."""
        columnas = []  # (encabezado, id, índice de afirmación | None para nube | lista de opciones)
        for s in self.slides:
            if s["tipo"] == "escalas":
                for i, a in enumerate(s["afirmaciones"]):
                    columnas.append((f"{s['id']} · {a['texto'][:60]}", s["id"], i))
            elif s["tipo"] == "nube":
                columnas.append((s["titulo"], s["id"], None))
            elif s["tipo"] == "opciones":
                columnas.append((s["titulo"], s["id"], s["opciones"]))
        votantes = sorted({v for r in self.respuestas.values() for v in r},
                          key=lambda v: self.nombres.get(v, "").lower())
        tmp = self.archivo.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Nombre"] + [c[0] for c in columnas])
            for v in votantes:
                fila = [self.nombres.get(v, "")]
                for _, sid, i in columnas:
                    r = self.respuestas[sid].get(v)
                    if r is None:
                        fila.append("")
                    elif i is None:
                        fila.append(", ".join(r))
                    elif isinstance(i, list):
                        fila.append(i[r])
                    else:
                        fila.append(r[i])
                w.writerow(fila)
        os.replace(tmp, self.archivo.with_suffix(".csv"))

    def _distribucion(self, s, i):
        mn, mx = s.get("min", 1), s.get("max", 5)
        vals = [r[i] for r in self.respuestas[s["id"]].values() if i < len(r)]
        return vals, [vals.count(x) for x in range(mn, mx + 1)]

    def _resultados(self, s):
        resp = list(self.respuestas[s["id"]].values())
        if s["tipo"] == "escalas":
            afirmaciones = []
            for i, a in enumerate(s["afirmaciones"]):
                vals, dist = self._distribucion(s, i)
                item = {
                    "n": len(vals),
                    "promedio": round(sum(vals) / len(vals), 2) if vals else None,
                    "distribucion": dist,
                }
                ref = a.get("comparar_con")
                otra = next((x for x in self.slides if x["id"] == (ref or {}).get("id")), None)
                if otra:
                    item["antes_distribucion"] = self._distribucion(otra, ref["afirmacion"])[1]
                afirmaciones.append(item)
            return {"respuestas": len(resp), "afirmaciones": afirmaciones}
        if s["tipo"] == "nube":
            conteo = Counter(p for r in resp for p in r)
            return {"respuestas": len(resp), "palabras": conteo.most_common(100)}
        if s["tipo"] == "opciones":
            return {"respuestas": len(resp), "conteo": [resp.count(i) for i in range(len(s["opciones"]))]}
        return {"respuestas": 0}

    def publico(self, votante, nombre=""):
        with self.lock:
            if votante and len(votante) <= 64:
                self.vistos[votante] = time.time()
                nombre = re.sub(r"\s+", " ", nombre).strip()[:60]
                if nombre:
                    self.nombres[votante] = nombre
            s = self.actual
            restante = self._restante()
            numero, preguntas = self._numeracion()
            return {
                "indice": self.indice,
                "total": len(self.slides),
                "pregunta": numero,
                "preguntas": preguntas,
                "diapositiva": s,
                "mi_respuesta": self.respuestas[s["id"]].get(votante),
                "restante": restante,
                "duracion": self.duracion,
                "cerrada": restante == 0,
            }

    def presentador(self, url):
        with self.lock:
            ahora = time.time()
            respondieron, esperados, nombres = self._participacion()
            restante = self._restante()
            numero, preguntas = self._numeracion()
            return {
                "titulo": self.titulo,
                "pregunta": numero,
                "preguntas": preguntas,
                "indice": self.indice,
                "total": len(self.slides),
                "diapositiva": self.actual,
                "resultados": self._resultados(self.actual),
                "restante": restante,
                "duracion": self.duracion,
                "cerrada": restante == 0,
                "revelado": self.revelado,
                "respondieron": respondieron,
                "esperados": esperados,
                "quienes": nombres,
                "conectados": sum(1 for t in self.vistos.values() if ahora - t < 8),
                "url": url,
                "qr": segno is not None,
            }

    def responder(self, votante, nombre, sid, respuesta):
        with self.lock:
            s = self.actual
            if not votante or not isinstance(votante, str) or len(votante) > 64:
                raise ValueError("Identificador inválido.")
            nombre = re.sub(r"\s+", " ", nombre).strip() if isinstance(nombre, str) else ""
            if not nombre or len(nombre) > 60:
                raise ValueError("Escribí tu nombre (hasta 60 caracteres).")
            if sid != s["id"]:
                raise ValueError("La presentación ya avanzó a otra diapositiva.")
            if self.duracion and time.time() > self.inicio + self.duracion + 1.5:
                raise ValueError("Se terminó el tiempo para esta pregunta.")
            if votante not in self.respuestas[sid] and len(self.respuestas[sid]) >= MAX_VOTANTES:
                raise ValueError("Se alcanzó el máximo de participantes.")
            if s["tipo"] == "escalas":
                mn, mx = s.get("min", 1), s.get("max", 5)
                if (not isinstance(respuesta, list) or len(respuesta) != len(s["afirmaciones"])
                        or not all(type(x) is int and mn <= x <= mx for x in respuesta)):
                    raise ValueError("Respondé todas las afirmaciones.")
                valor = respuesta
            elif s["tipo"] == "nube":
                if not isinstance(respuesta, list):
                    raise ValueError("Respuesta inválida.")
                valor = []
                for p in respuesta:
                    p = normalizar(p) if isinstance(p, str) else ""
                    if p and p not in valor:
                        valor.append(p)
                valor = valor[: s.get("max_palabras", 3)]
                if not valor:
                    raise ValueError("Escribí al menos una palabra.")
            elif s["tipo"] == "opciones":
                if type(respuesta) is not int or not 0 <= respuesta < len(s["opciones"]):
                    raise ValueError("Elegí una opción.")
                valor = respuesta
            else:
                raise ValueError("Esta diapositiva no tiene pregunta.")
            self.respuestas[sid][votante] = valor
            self.nombres[votante] = nombre
            self._guardar()

    def control(self, accion, indice=None):
        with self.lock:
            anterior = self.indice
            if accion == "siguiente":
                self.indice = min(self.indice + 1, len(self.slides) - 1)
            elif accion == "anterior":
                self.indice = max(self.indice - 1, 0)
            elif accion == "ir" and isinstance(indice, int) and 0 <= indice < len(self.slides):
                self.indice = indice
            elif accion == "revelar":
                self.revelado = not self.revelado
            elif accion == "reiniciar_tiempo":
                self.inicio = time.time()
                self.duracion = self.actual.get("tiempo", self.tiempo) if self.actual["tipo"] != "contenido" else 0
                self.revelado = False
            elif accion == "mas_tiempo":
                if self.duracion:
                    self.duracion = (time.time() - self.inicio) + self._restante() + 15
            elif accion == "borrar":
                self.respuestas[self.actual["id"]] = {}
                self._guardar()
            else:
                raise ValueError("Acción desconocida.")
            if self.indice != anterior:
                self._iniciar_diapositiva()


class Handler(BaseHTTPRequestHandler):
    estado = None
    clave = ""
    url_votar = ""

    def log_message(self, *args):
        pass

    def _enviar(self, cuerpo, tipo, codigo=200):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, datos, codigo=200):
        self._enviar(json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8", codigo)

    def _html(self, nombre):
        self._enviar((STATIC / nombre).read_bytes(), "text/html; charset=utf-8")

    def _autorizado(self, q):
        return secrets.compare_digest(q.get("k", [""])[0], self.clave)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/votar"):
            return self._html("votar.html")
        if u.path == "/presentador":
            return self._html("presentador.html")
        if u.path == "/qr.svg" and segno:
            buf = io.BytesIO()
            segno.make(self.url_votar, error="m").save(buf, kind="svg", scale=10, border=2)
            return self._enviar(buf.getvalue(), "image/svg+xml")
        if u.path == "/api/estado":
            return self._json(self.estado.publico(q.get("v", [""])[0], q.get("n", [""])[0]))
        if u.path == "/api/presentador":
            if not self._autorizado(q):
                return self._json({"error": "Clave de presentador inválida."}, 403)
            return self._json(self.estado.presentador(self.url_votar))
        self._json({"error": "No encontrado."}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            if largo > 10_000:
                raise ValueError("Pedido demasiado grande.")
            datos = json.loads(self.rfile.read(largo) or b"{}")
            if u.path == "/api/responder":
                self.estado.responder(datos.get("v"), datos.get("nombre"), datos.get("id"), datos.get("respuesta"))
                return self._json({"ok": True})
            if u.path == "/api/control":
                if not self._autorizado(q):
                    return self._json({"error": "Clave de presentador inválida."}, 403)
                self.estado.control(datos.get("accion"), datos.get("indice"))
                return self._json({"ok": True})
            self._json({"error": "No encontrado."}, 404)
        except (ValueError, json.JSONDecodeError) as e:
            self._json({"error": str(e) or "Pedido inválido."}, 400)


def abrir_tunel(puerto, preferido=None, espera=40):
    """Abre un túnel gratuito y devuelve (proceso, url_publica) o (None, None)."""
    for nombre, comando, patron in TUNELES:
        if (preferido and nombre != preferido) or not shutil.which(nombre):
            continue
        print(f"  Abriendo túnel público con {nombre}…")
        # stdin queda abierto (PIPE) para que la sesión ssh no se cierre.
        proc = subprocess.Popen(comando(puerto), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        hallada = queue.Queue()

        def leer():
            for linea in proc.stdout:  # se sigue leyendo para que la salida no se trabe
                m = re.search(patron, linea)
                if m:
                    hallada.put(m.group(0) + "/")

        threading.Thread(target=leer, daemon=True).start()
        try:
            return proc, hallada.get(timeout=espera)
        except queue.Empty:
            proc.terminate()
            print(f"  No se pudo abrir el túnel con {nombre}.")
    return None, None


def mantener_tunel(proc, puerto, preferido):
    """Si el túnel se corta, lo vuelve a abrir y actualiza la URL (y el QR)."""
    while True:
        proc.wait()
        print("\n  ⚠ Se cortó el túnel público. Reintentando…")
        while True:
            proc, url = abrir_tunel(puerto, preferido)
            if url:
                Handler.url_votar = url
                print(f"  Nueva URL para alumnos: {url}  (el QR del presentador ya se actualizó)")
                break
            time.sleep(10)


def cargar_presentacion(ruta):
    pres = json.loads(Path(ruta).read_text(encoding="utf-8"))
    ids = set()
    for n, s in enumerate(pres.get("diapositivas", []), 1):
        if s.get("tipo") not in TIPOS:
            raise SystemExit(f"Diapositiva {n}: tipo debe ser uno de {sorted(TIPOS)}")
        if not s.get("id") or s["id"] in ids:
            raise SystemExit(f"Diapositiva {n}: falta 'id' o está repetido")
        ids.add(s["id"])
        if s["tipo"] == "escalas" and not s.get("afirmaciones"):
            raise SystemExit(f"Diapositiva {n}: una diapositiva de escalas necesita 'afirmaciones'")
        if s["tipo"] == "opciones" and not s.get("opciones"):
            raise SystemExit(f"Diapositiva {n}: una diapositiva de opciones necesita 'opciones'")
    if not ids:
        raise SystemExit("La presentación no tiene diapositivas.")
    return pres


def main():
    ap = argparse.ArgumentParser(description="Preguntas en vivo para la clase, con QR.")
    ap.add_argument("presentacion", nargs="?", default=str(BASE / "presentacion.json"))
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--tiempo", type=int, help="Segundos para responder cada pregunta (0 = sin límite)")
    ap.add_argument("--publico", action="store_true",
                    help="Crear una URL pública en internet (clases online) con un túnel gratuito")
    ap.add_argument("--tunel", choices=[t[0] for t in TUNELES], help="Forzar el túnel a usar con --publico")
    ap.add_argument("--ip", help="IP a mostrar en el QR (por defecto se detecta sola)")
    ap.add_argument("--clave", help="Clave del presentador (por defecto, una al azar)")
    ap.add_argument("--resultados", help="Archivo donde guardar las respuestas")
    ap.add_argument("--reiniciar", action="store_true", help="Ignorar respuestas guardadas antes")
    ap.add_argument("--no-abrir", action="store_true", help="No abrir el navegador automáticamente")
    args = ap.parse_args()

    ruta = Path(args.presentacion).resolve()
    pres = cargar_presentacion(ruta)
    resultados = Path(args.resultados) if args.resultados else ruta.with_name(f"resultados-{ruta.stem}.json")

    Handler.estado = Estado(pres, resultados, args.reiniciar, args.tiempo)
    Handler.clave = args.clave or secrets.token_urlsafe(6)
    Handler.url_votar = f"http://{args.ip or ip_local()}:{args.puerto}/"
    url_presentador = f"http://localhost:{args.puerto}/presentador?k={Handler.clave}"

    # Con túnel público solo se escucha en esta máquina: todo el tráfico externo entra por el túnel.
    servidor = ThreadingHTTPServer(("127.0.0.1" if args.publico else "0.0.0.0", args.puerto), Handler)
    print(f"\n  {pres.get('titulo', '')}")
    if args.publico:
        proc, url = abrir_tunel(args.puerto, args.tunel)
        if not url:
            servidor.server_close()
            raise SystemExit("\n  No se pudo crear la URL pública. Instalá cloudflared (ver LEEME.md) "
                             "o verificá que el comando ssh funcione.")
        Handler.url_votar = url
        threading.Thread(target=mantener_tunel, args=(proc, args.puerto, args.tunel), daemon=True).start()
    print(f"  Alumnos:      {Handler.url_votar}")
    print(f"  Presentador:  {url_presentador}   (no compartas este enlace)")
    print(f"  Respuestas guardadas en: {resultados}")
    print(f"  Planilla por alumno:     {resultados.with_suffix('.csv')}")
    if segno:
        segno.make(Handler.url_votar, error="m").terminal(compact=True)
    else:
        print("\n  (Para mostrar el QR instalá segno:  pip install segno)")
    print("\n  Ctrl+C para terminar.\n")
    if not args.no_abrir:
        threading.Timer(0.5, webbrowser.open, [url_presentador]).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n  Listo. ¡Buena clase!")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
