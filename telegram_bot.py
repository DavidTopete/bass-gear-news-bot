import os
import json
import html
import time
import re
import requests
import feedparser

from bs4 import BeautifulSoup
from datetime import datetime
from deep_translator import GoogleTranslator


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ARCHIVO_HISTORIAL = "noticias_enviadas_bass.json"

TOTAL_NOTICIAS = 5
MAXIMO_HISTORIAL = 2000
MAXIMO_RESUMEN = 750

TIMEOUT_HTTP = 25
TIEMPO_ENTRE_FUENTES = 1
TIEMPO_ENTRE_MENSAJES = 2


# ============================================================
# FUENTES RSS
# ============================================================
#
# Se eliminó completamente:
#
# For Bass Players Only
# https://forbassplayersonly.com/feed/
#
# porque estaba devolviendo una página de Error 500 como noticia.
# ============================================================

FUENTES = [
    {
        "nombre": "Bass Player Mexico",
        "rss": "https://bassplayermexico.com/feed/"
    },
    {
        "nombre": "No Treble",
        "rss": "https://www.notreble.com/feed/"
    },
    {
        "nombre": "Bass Magazine",
        "rss": "https://bassmagazine.com/feed/"
    },
    {
        "nombre": "Bass Musician Magazine",
        "rss": "https://bassmusicianmagazine.com/feed/"
    },
    {
        "nombre": "Bass Gear Magazine",
        "rss": "https://www.bassgearmag.com/feed/"
    },
    {
        "nombre": "TalkingBass",
        "rss": "https://www.talkingbass.net/feed/"
    },
    {
        "nombre": "StudyBass",
        "rss": "https://www.studybass.com/feed/"
    },
    {
        "nombre": "BassBuzz",
        "rss": "https://www.bassbuzz.com/feed/"
    },
    {
        "nombre": "MusicRadar Bass",
        "rss": "https://www.musicradar.com/rss/news/guitars/bass-guitars"
    }
]


# ============================================================
# PALABRAS CLAVE
# ============================================================

PALABRAS_CLAVE_BAJO = [
    "bass",
    "bassist",
    "bass guitar",
    "electric bass",
    "upright bass",
    "double bass",
    "fretless",
    "slap bass",
    "low end",
    "bass amp",
    "bass amplifier",
    "bass pedal",
    "bass player",
    "bassline",
    "bajo",
    "bajista",
    "contrabajo"
]


# ============================================================
# CONTENIDO EXCLUIDO
# ============================================================

PALABRAS_EXCLUIDAS = [
    "japan",
    "japanese",
    "young guitar",
    "ultimate guitar",
    "guitar world",
    "premier guitar",
    "acoustic guitar",
    "electric guitar",
    "guitar solo",
    "drum",
    "drummer",
    "keyboard",
    "synth",
    "microphone"
]


# ============================================================
# TEXTOS DE ERROR QUE NUNCA DEBEN PUBLICARSE
# ============================================================

TEXTOS_DE_ERROR = [
    "error 400",
    "error 401",
    "error 403",
    "error 404",
    "error 405",
    "error 408",
    "error 429",
    "error 500",
    "error 501",
    "error 502",
    "error 503",
    "error 504",
    "server error",
    "internal server error",
    "bad request",
    "unauthorized",
    "access denied",
    "forbidden",
    "not found",
    "page not found",
    "service unavailable",
    "temporarily unavailable",
    "bad gateway",
    "gateway timeout",
    "request timeout",
    "too many requests",
    "please try again",
    "please try again later",
    "try again later",
    "that's an error",
    "that’s an error",
    "there was an error",
    "an error occurred",
    "something went wrong",
    "the server encountered an error",
    "cloudflare",
    "captcha",
    "checking your browser",
    "verify you are human",
    "enable javascript",
    "enable cookies"
]


# ============================================================
# FUENTES BLOQUEADAS
# ============================================================

FUENTES_BLOQUEADAS = [
    "for bass players only",
    "forbassplayersonly",
    "forbassplayersonly.com"
]


# ============================================================
# SESIÓN HTTP
# ============================================================

SESION = requests.Session()

SESION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36 "
        "BassNewsBot/2.0"
    ),
    "Accept": (
        "application/rss+xml, "
        "application/atom+xml, "
        "application/xml, "
        "text/xml, "
        "text/html;q=0.8, "
        "*/*;q=0.5"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache"
})


# ============================================================
# FUNCIONES DE NORMALIZACIÓN
# ============================================================

def normalizar_texto(texto):
    """
    Convierte el texto a minúsculas, normaliza espacios y elimina
    ciertos caracteres para detectar errores aunque vengan unidos.
    """

    if texto is None:
        return ""

    texto = str(texto).lower()

    texto = texto.replace("’", "'")
    texto = texto.replace("‘", "'")
    texto = texto.replace("“", '"')
    texto = texto.replace("”", '"')
    texto = texto.replace("\xa0", " ")

    texto = re.sub(r"\s+", " ", texto)
    texto = texto.strip()

    return texto


def texto_compacto(texto):
    """
    Elimina espacios y signos para detectar mensajes como:

    Error500(ServerError)That'sanerror
    """

    texto = normalizar_texto(texto)

    return re.sub(
        r"[^a-záéíóúüñ0-9]+",
        "",
        texto
    )


# ============================================================
# HISTORIAL
# ============================================================

def cargar_historial():
    if not os.path.exists(ARCHIVO_HISTORIAL):
        print("No existe historial. Creando archivo nuevo...")

        try:
            with open(
                ARCHIVO_HISTORIAL,
                "w",
                encoding="utf-8"
            ) as archivo:
                json.dump(
                    [],
                    archivo,
                    ensure_ascii=False,
                    indent=2
                )

        except OSError as error:
            print("No se pudo crear el historial:", error)

        return []

    try:
        with open(
            ARCHIVO_HISTORIAL,
            "r",
            encoding="utf-8"
        ) as archivo:
            historial = json.load(archivo)

        if not isinstance(historial, list):
            print("El historial no es válido. Se reiniciará.")
            historial = []

        historial_limpio = []

        for enlace in historial:
            if isinstance(enlace, str) and enlace.strip():
                if enlace not in historial_limpio:
                    historial_limpio.append(enlace)

        print(
            f"Historial cargado: "
            f"{len(historial_limpio)} noticias"
        )

        return historial_limpio

    except (OSError, json.JSONDecodeError) as error:
        print("Error leyendo historial:", error)
        print("Se utilizará un historial vacío.")

        return []


def guardar_historial(historial):
    historial_limpio = []

    for enlace in historial:
        if not isinstance(enlace, str):
            continue

        enlace = enlace.strip()

        if enlace and enlace not in historial_limpio:
            historial_limpio.append(enlace)

    historial_limpio = historial_limpio[-MAXIMO_HISTORIAL:]

    try:
        with open(
            ARCHIVO_HISTORIAL,
            "w",
            encoding="utf-8"
        ) as archivo:
            json.dump(
                historial_limpio,
                archivo,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"Historial guardado: "
            f"{len(historial_limpio)} noticias"
        )

        return True

    except OSError as error:
        print("Error guardando historial:", error)
        return False


# ============================================================
# LIMPIEZA HTML
# ============================================================

def limpiar_html(texto):
    if not texto:
        return ""

    try:
        soup = BeautifulSoup(
            str(texto),
            "html.parser"
        )

        texto_limpio = soup.get_text(
            " ",
            strip=True
        )

        texto_limpio = html.unescape(texto_limpio)
        texto_limpio = re.sub(
            r"\s+",
            " ",
            texto_limpio
        )

        return texto_limpio.strip()

    except Exception:
        return str(texto).strip()


# ============================================================
# TRADUCCIÓN
# ============================================================

def traducir(texto):
    if not texto:
        return ""

    try:
        traduccion = GoogleTranslator(
            source="auto",
            target="es"
        ).translate(texto)

        if traduccion:
            return traduccion.strip()

        return texto

    except Exception as error:
        print("No se pudo traducir:", error)
        return texto


# ============================================================
# DETECCIÓN DE ERRORES
# ============================================================

def contiene_texto_de_error(*textos):
    """
    Devuelve True si cualquiera de los textos parece una página
    de error de servidor.
    """

    contenido = " ".join(
        str(texto)
        for texto in textos
        if texto
    )

    contenido_normal = normalizar_texto(contenido)
    contenido_sin_signos = texto_compacto(contenido)

    # Detección normal.
    for texto_error in TEXTOS_DE_ERROR:
        error_normal = normalizar_texto(texto_error)
        error_compacto = texto_compacto(texto_error)

        if error_normal in contenido_normal:
            return True

        if error_compacto and error_compacto in contenido_sin_signos:
            return True

    # Detección mediante patrones.
    patrones = [
        r"\berror\s*[\(\-:]?\s*500\b",
        r"\b500\s*server\s*error\b",
        r"\binternal\s*server\s*error\b",
        r"\bserver\s*error\b",
        r"\bthere\s*was\s*an\s*error\b",
        r"\bthat'?s\s*an\s*error\b",
        r"\bplease\s*try\s*again\s*later\b",
        r"\bservice\s*unavailable\b",
        r"\bbad\s*gateway\b",
        r"\bgateway\s*timeout\b"
    ]

    for patron in patrones:
        if re.search(
            patron,
            contenido_normal,
            flags=re.IGNORECASE
        ):
            return True

    # Detección combinada:
    # si contiene código 500 y además menciona error.
    if "500" in contenido_normal and "error" in contenido_normal:
        return True

    # Texto exacto similar al mostrado en Telegram.
    if (
        "error500" in contenido_sin_signos
        and "servererror" in contenido_sin_signos
    ):
        return True

    return False


def fuente_esta_bloqueada(nombre, url=""):
    contenido = normalizar_texto(
        f"{nombre} {url}"
    )

    contenido_compacto = texto_compacto(
        f"{nombre} {url}"
    )

    for fuente_bloqueada in FUENTES_BLOQUEADAS:
        bloqueada_normal = normalizar_texto(
            fuente_bloqueada
        )

        bloqueada_compacta = texto_compacto(
            fuente_bloqueada
        )

        if bloqueada_normal in contenido:
            return True

        if bloqueada_compacta in contenido_compacto:
            return True

    return False


# ============================================================
# VALIDACIÓN DEL CONTENIDO
# ============================================================

def es_contenido_de_bajo(titulo, resumen, fuente):
    contenido_noticia = normalizar_texto(
        f"{titulo} {resumen}"
    )

    if fuente_esta_bloqueada(fuente):
        print(
            f"Fuente bloqueada descartada: {fuente}"
        )
        return False

    if contiene_texto_de_error(
        titulo,
        resumen,
        fuente
    ):
        print(
            f"Texto de error descartado: {titulo}"
        )
        return False

    for palabra in PALABRAS_EXCLUIDAS:
        if normalizar_texto(palabra) in contenido_noticia:
            print(
                f"Contenido excluido: {titulo}"
            )
            return False

    # Las fuentes incluidas en FUENTES ya están relacionadas
    # directamente con el bajo.
    return True


# ============================================================
# DESCARGA DEL RSS
# ============================================================

def obtener_feed(fuente):
    nombre = fuente.get("nombre", "")
    url_rss = fuente.get("rss", "")

    if fuente_esta_bloqueada(nombre, url_rss):
        print(
            f"Fuente bloqueada y omitida: {nombre}"
        )
        return None

    try:
        respuesta = SESION.get(
            url_rss,
            timeout=TIMEOUT_HTTP,
            allow_redirects=True
        )

        print(
            f"HTTP {respuesta.status_code} - "
            f"{nombre}"
        )

        if respuesta.status_code != 200:
            print(
                f"Fuente omitida: {nombre}. "
                f"Respondió HTTP {respuesta.status_code}."
            )
            return None

        if not respuesta.content:
            print(f"Fuente vacía: {nombre}")
            return None

        tipo_contenido = normalizar_texto(
            respuesta.headers.get(
                "Content-Type",
                ""
            )
        )

        texto_inicial = respuesta.text[:5000]

        if contiene_texto_de_error(
            texto_inicial,
            nombre,
            respuesta.url
        ):
            print(
                f"Respuesta de error descartada: {nombre}"
            )
            return None

        # Si devuelve HTML en lugar de RSS, puede ser una
        # página de bloqueo o error.
        if (
            "text/html" in tipo_contenido
            and "<rss" not in texto_inicial.lower()
            and "<feed" not in texto_inicial.lower()
        ):
            print(
                f"{nombre} devolvió HTML en lugar de RSS. "
                f"Fuente omitida."
            )
            return None

        feed = feedparser.parse(
            respuesta.content
        )

        if getattr(feed, "bozo", 0):
            print(
                f"Advertencia RSS en {nombre}: "
                f"{getattr(feed, 'bozo_exception', 'RSS inválido')}"
            )

        entries = getattr(feed, "entries", [])

        if not entries:
            print(
                f"Sin entradas RSS válidas: {nombre}"
            )
            return None

        return feed

    except requests.Timeout:
        print(
            f"Tiempo agotado descargando: {nombre}"
        )
        return None

    except requests.RequestException as error:
        print(
            f"Error de conexión en {nombre}: {error}"
        )
        return None

    except Exception as error:
        print(
            f"Error inesperado en {nombre}: {error}"
        )
        return None


# ============================================================
# DATOS DE LA ENTRADA RSS
# ============================================================

def obtener_datos_entrada(entrada):
    titulo = limpiar_html(
        entrada.get("title", "")
    )

    resumen = limpiar_html(
        entrada.get(
            "summary",
            entrada.get("description", "")
        )
    )

    if not resumen:
        contenido = entrada.get("content", [])

        if isinstance(contenido, list) and contenido:
            resumen = limpiar_html(
                contenido[0].get("value", "")
            )

    link = entrada.get("link", "")

    if link:
        link = str(link).strip()

    if not link:
        enlaces = entrada.get("links", [])

        if isinstance(enlaces, list):
            for enlace in enlaces:
                href = enlace.get("href", "")
                rel = enlace.get("rel", "")

                if href and rel in ("alternate", ""):
                    link = str(href).strip()
                    break

    return titulo, resumen, link


# ============================================================
# VALIDACIÓN DE ENTRADAS
# ============================================================

def entrada_es_valida(
    titulo,
    resumen,
    link,
    fuente,
    historial,
    links_corrida
):
    if not titulo:
        print("Entrada ignorada: sin título.")
        return False

    if not link:
        print(
            f"Entrada ignorada sin enlace: {titulo}"
        )
        return False

    if not link.startswith(
        ("http://", "https://")
    ):
        print(
            f"Enlace inválido ignorado: {link}"
        )
        return False

    if fuente_esta_bloqueada(fuente, link):
        print(
            f"Fuente o enlace bloqueado: {titulo}"
        )
        return False

    if contiene_texto_de_error(
        titulo,
        resumen,
        fuente,
        link
    ):
        print(
            f"Página de error ignorada: {titulo}"
        )
        return False

    if link in historial:
        print(
            f"Noticia repetida ignorada: {titulo}"
        )
        return False

    if link in links_corrida:
        return False

    if not es_contenido_de_bajo(
        titulo,
        resumen,
        fuente
    ):
        return False

    return True


# ============================================================
# OBTENER NOTICIAS
# ============================================================

def obtener_noticias(historial):
    historial_set = set(historial)
    links_corrida = set()
    noticias = []
    feeds_validos = {}

    # Primera ronda:
    # toma como máximo una noticia por fuente.
    for fuente in FUENTES:
        if len(noticias) >= TOTAL_NOTICIAS:
            break

        nombre = fuente["nombre"]

        print("\n" + "=" * 60)
        print(f"Revisando fuente: {nombre}")

        feed = obtener_feed(fuente)
        feeds_validos[nombre] = feed

        if feed is None:
            continue

        for entrada in feed.entries:
            titulo, resumen, link = obtener_datos_entrada(
                entrada
            )

            if not entrada_es_valida(
                titulo=titulo,
                resumen=resumen,
                link=link,
                fuente=nombre,
                historial=historial_set,
                links_corrida=links_corrida
            ):
                continue

            noticias.append({
                "fuente": nombre,
                "titulo_original": titulo,
                "resumen_original": resumen,
                "link": link
            })

            links_corrida.add(link)

            print(f"Noticia agregada: {titulo}")
            break

        time.sleep(TIEMPO_ENTRE_FUENTES)

    # Segunda ronda:
    # busca noticias adicionales si todavía faltan.
    if len(noticias) < TOTAL_NOTICIAS:
        print(
            "\nFaltan noticias. "
            "Buscando entradas adicionales..."
        )

        for fuente in FUENTES:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            nombre = fuente["nombre"]
            feed = feeds_validos.get(nombre)

            if feed is None:
                continue

            for entrada in feed.entries:
                if len(noticias) >= TOTAL_NOTICIAS:
                    break

                titulo, resumen, link = obtener_datos_entrada(
                    entrada
                )

                if not entrada_es_valida(
                    titulo=titulo,
                    resumen=resumen,
                    link=link,
                    fuente=nombre,
                    historial=historial_set,
                    links_corrida=links_corrida
                ):
                    continue

                noticias.append({
                    "fuente": nombre,
                    "titulo_original": titulo,
                    "resumen_original": resumen,
                    "link": link
                })

                links_corrida.add(link)

                print(
                    f"Noticia adicional agregada: "
                    f"{titulo}"
                )

            time.sleep(TIEMPO_ENTRE_FUENTES)

    return noticias[:TOTAL_NOTICIAS]


# ============================================================
# VALIDACIÓN FINAL ANTES DE TELEGRAM
# ============================================================

def noticia_es_segura_para_enviar(noticia):
    fuente = noticia.get("fuente", "")
    titulo = noticia.get("titulo_original", "")
    resumen = noticia.get("resumen_original", "")
    link = noticia.get("link", "")

    if fuente_esta_bloqueada(fuente, link):
        print(
            "ENVÍO CANCELADO: fuente bloqueada."
        )
        print(f"Fuente: {fuente}")
        print(f"Título: {titulo}")
        return False

    if contiene_texto_de_error(
        fuente,
        titulo,
        resumen,
        link
    ):
        print(
            "ENVÍO CANCELADO: se detectó texto de error."
        )
        print(f"Fuente: {fuente}")
        print(f"Título: {titulo}")
        return False

    return True


def mensaje_es_seguro(mensaje):
    """
    Última barrera: analiza el mensaje completo que será enviado
    a Telegram.
    """

    mensaje_limpio = limpiar_html(mensaje)

    if contiene_texto_de_error(mensaje_limpio):
        print(
            "MENSAJE BLOQUEADO: contiene texto de error."
        )
        return False

    if fuente_esta_bloqueada(mensaje_limpio):
        print(
            "MENSAJE BLOQUEADO: contiene una fuente bloqueada."
        )
        return False

    return True


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(mensaje):
    if not mensaje_es_seguro(mensaje):
        print(
            "El mensaje no fue enviado a Telegram."
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendMessage"
    )

    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        respuesta = SESION.post(
            url,
            data=datos,
            timeout=TIMEOUT_HTTP
        )

        print(
            f"Telegram HTTP: "
            f"{respuesta.status_code}"
        )

        try:
            resultado = respuesta.json()

        except ValueError:
            print(
                "Telegram devolvió una respuesta inválida:"
            )
            print(respuesta.text)
            return False

        if respuesta.status_code != 200:
            print("Error HTTP de Telegram:")
            print(resultado)
            return False

        if not resultado.get("ok", False):
            print("Telegram rechazó el mensaje:")
            print(resultado)
            return False

        print(
            "Mensaje enviado correctamente."
        )

        return True

    except requests.Timeout:
        print(
            "Tiempo agotado al enviar a Telegram."
        )
        return False

    except requests.RequestException as error:
        print(
            f"Error de conexión con Telegram: {error}"
        )
        return False

    except Exception as error:
        print(
            f"Error inesperado enviando mensaje: {error}"
        )
        return False


# ============================================================
# ENCABEZADO
# ============================================================

def enviar_encabezado():
    fecha = datetime.now().strftime("%d/%m/%Y")

    mensaje = (
        "<b>🎸 BASS NEWS</b>\n"
        f"<b>Fecha:</b> {html.escape(fecha)}"
    )

    # El encabezado se envía directamente porque no contiene
    # información obtenida de fuentes RSS.
    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendMessage"
    )

    try:
        respuesta = SESION.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": mensaje,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=TIMEOUT_HTTP
        )

        resultado = respuesta.json()

        if (
            respuesta.status_code == 200
            and resultado.get("ok", False)
        ):
            print("Encabezado enviado correctamente.")
            return True

        print(
            "No se pudo enviar el encabezado:",
            resultado
        )
        return False

    except Exception as error:
        print(
            f"Error enviando encabezado: {error}"
        )
        return False


# ============================================================
# CREAR MENSAJE DE NOTICIA
# ============================================================

def crear_mensaje_noticia(noticia):
    if not noticia_es_segura_para_enviar(noticia):
        return None

    titulo_original = noticia.get(
        "titulo_original",
        ""
    )

    resumen_original = noticia.get(
        "resumen_original",
        ""
    )

    fuente = noticia.get(
        "fuente",
        ""
    )

    link = noticia.get(
        "link",
        ""
    )

    titulo_es = traducir(titulo_original)
    resumen_es = traducir(resumen_original)

    # Se valida nuevamente después de traducir.
    if contiene_texto_de_error(
        titulo_es,
        resumen_es,
        fuente,
        link
    ):
        print(
            "Noticia descartada después de traducir: "
            f"{titulo_original}"
        )
        return None

    if len(resumen_es) > MAXIMO_RESUMEN:
        resumen_es = (
            resumen_es[:MAXIMO_RESUMEN].rstrip()
            + "..."
        )

    titulo_seguro = html.escape(
        titulo_es,
        quote=True
    )

    fuente_segura = html.escape(
        fuente,
        quote=True
    )

    resumen_seguro = html.escape(
        resumen_es,
        quote=True
    )

    link_seguro = html.escape(
        link,
        quote=True
    )

    mensaje = (
        f"<b>{titulo_seguro}</b>\n"
        f"<b>Fuente:</b> {fuente_segura}\n\n"
    )

    if resumen_seguro:
        mensaje += (
            f"{resumen_seguro}\n\n"
        )

    mensaje += (
        f'<a href="{link_seguro}">'
        f"Leer noticia completa"
        f"</a>"
    )

    # Validación absoluta del mensaje terminado.
    if not mensaje_es_seguro(mensaje):
        print(
            f"Mensaje final descartado: {titulo_original}"
        )
        return None

    return mensaje


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    print("=" * 60)
    print("INICIANDO BASS NEWS BOT")
    print("=" * 60)

    if not TOKEN:
        print(
            "Falta configurar la variable TOKEN."
        )
        return

    if not CHAT_ID:
        print(
            "Falta configurar la variable CHAT_ID."
        )
        return

    historial = cargar_historial()
    noticias = obtener_noticias(historial)

    # Filtro final de toda la lista.
    noticias_seguras = []

    for noticia in noticias:
        if noticia_es_segura_para_enviar(noticia):
            noticias_seguras.append(noticia)
        else:
            print(
                "Una noticia fue eliminada antes "
                "del proceso de envío."
            )

    noticias = noticias_seguras[:TOTAL_NOTICIAS]

    print("\n" + "=" * 60)
    print(
        f"Noticias seguras encontradas: "
        f"{len(noticias)}"
    )
    print("=" * 60)

    if not noticias:
        print(
            "No hay noticias nuevas y seguras "
            "para enviar."
        )
        guardar_historial(historial)
        return

    if not enviar_encabezado():
        print(
            "No se pudo enviar el encabezado. "
            "El proceso fue cancelado."
        )
        return

    time.sleep(TIEMPO_ENTRE_MENSAJES)

    enviadas = 0
    descartadas = 0
    fallidas = 0

    for numero, noticia in enumerate(
        noticias,
        start=1
    ):
        print("\n" + "-" * 60)
        print(
            f"Procesando noticia "
            f"{numero}/{len(noticias)}"
        )

        if not noticia_es_segura_para_enviar(noticia):
            descartadas += 1
            continue

        mensaje = crear_mensaje_noticia(noticia)

        if not mensaje:
            print(
                "Noticia descartada. "
                "No se generó ningún mensaje."
            )
            descartadas += 1
            continue

        if not mensaje_es_seguro(mensaje):
            print(
                "Mensaje descartado por la "
                "validación final."
            )
            descartadas += 1
            continue

        enviado = enviar_telegram(mensaje)

        if enviado:
            historial.append(
                noticia["link"]
            )

            guardar_historial(historial)
            enviadas += 1

        else:
            fallidas += 1

        time.sleep(TIEMPO_ENTRE_MENSAJES)

    print("\n" + "=" * 60)
    print("PROCESO TERMINADO")
    print(f"Noticias enviadas: {enviadas}")
    print(f"Noticias descartadas: {descartadas}")
    print(f"Envíos fallidos: {fallidas}")
    print("=" * 60)


if __name__ == "__main__":
    main()
