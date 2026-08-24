import os
import re
import json
import html
import time
import logging
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime

# deep_translator es opcional: si falla la importación, el bot sigue
# funcionando con el traductor HTTP directo (fallback).
try:
    from deep_translator import GoogleTranslator
    DEEP_TRANSLATOR_DISPONIBLE = True
except Exception as _error_import:  # ImportError u otros
    GoogleTranslator = None
    DEEP_TRANSLATOR_DISPONIBLE = False
    _ERROR_IMPORT_TRADUCTOR = _error_import

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("bass_news_bot")

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ARCHIVO_HISTORIAL = "noticias_enviadas_bass.json"
TOTAL_NOTICIAS = 5
MAX_LARGO_TITULO = 200
MAX_LARGO_RESUMEN = 750

# Traducción
IDIOMA_DESTINO = "es"
LIMITE_CHARS_TRADUCTOR = 4500   # Google corta/rechaza por encima de ~5000
MAX_INTENTOS_TRADUCCION = 3
ESPERA_BASE_TRADUCCION = 2      # segundos (backoff exponencial: 2, 4, 8)
URL_TRADUCTOR_HTTP = "https://translate.googleapis.com/translate_a/single"

# Telegram
LIMITE_TELEGRAM = 4096

# Filtrado
# Si es False, las palabras excluidas solo se evalúan sobre título + fuente,
# no sobre el resumen (evita descartar notas de bajo que mencionan "drum",
# "keyboard", etc. de pasada). Poner True para volver al comportamiento previo.
APLICAR_EXCLUSIONES_A_RESUMEN = False

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

FUENTES = [
    {"nombre": "Bass Player Mexico", "rss": "https://bassplayermexico.com/feed/"},
    {"nombre": "No Treble", "rss": "https://www.notreble.com/feed/"},
    {"nombre": "Bass Magazine", "rss": "https://bassmagazine.com/feed/"},
    {"nombre": "Bass Musician Magazine", "rss": "https://bassmusicianmagazine.com/feed/"},
    {"nombre": "Bass Gear Magazine", "rss": "https://www.bassgearmag.com/feed/"},
    {"nombre": "For Bass Players Only", "rss": "https://forbassplayersonly.com/feed/"},
    {"nombre": "TalkingBass", "rss": "https://www.talkingbass.net/feed/"},
    {"nombre": "StudyBass", "rss": "https://www.studybass.com/feed/"},
    {"nombre": "BassBuzz", "rss": "https://www.bassbuzz.com/feed/"},
    {"nombre": "MusicRadar Bass", "rss": "https://www.musicradar.com/rss/news/guitars/bass-guitars"}
]

# Fuentes que ya publican en español (no requieren traducción)
FUENTES_EN_ESPANOL = {"bass player mexico"}

PALABRAS_CLAVE_BAJO = [
    "bass", "bassist", "bass guitar", "electric bass",
    "upright bass", "double bass", "fretless", "slap bass",
    "low end", "bass amp", "bass pedal", "bass player",
    "bajo", "bajista", "contrabajo"
]

PALABRAS_EXCLUIDAS = [
    "japan", "japanese", "young guitar", "ultimate guitar",
    "guitar world", "premier guitar", "acoustic guitar",
    "electric guitar", "guitar solo", "drum", "drummer",
    "keyboard", "synth", "microphone"
]

# Firmas que indican que el "texto traducido" en realidad es una página
# de error de Google (bloqueo temporal / rate limit) devuelta como 200 OK.
FIRMAS_ERROR_TRADUCCION = [
    "that's an error",
    "there was an error",
    "please try again later",
    "that's all we know",
    "error 500",
    "error 429",
    "<html", "<!doctype"
]

# Estadísticas de la corrida (diagnóstico)
STATS = {"traducciones_ok": 0, "traducciones_fallidas": 0}


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

def cargar_historial():
    if not os.path.exists(ARCHIVO_HISTORIAL):
        log.info("No existe historial. Creando archivo nuevo...")
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as archivo:
            json.dump([], archivo, ensure_ascii=False, indent=2)
        return []

    try:
        with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as archivo:
            historial = json.load(archivo)

        if not isinstance(historial, list):
            historial = []

        log.info(f"Historial cargado: {len(historial)} noticias registradas")
        return historial

    except Exception as error:
        log.error(f"Error leyendo historial. Se respalda y se reinicia: {error}")
        if os.path.exists(ARCHIVO_HISTORIAL):
            backup = f"{ARCHIVO_HISTORIAL}.bak_{int(time.time())}"
            try:
                os.replace(ARCHIVO_HISTORIAL, backup)
                log.warning(f"Historial corrupto respaldado en: {backup}")
            except OSError:
                pass
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as archivo:
            json.dump([], archivo, ensure_ascii=False, indent=2)
        return []


def guardar_historial(historial):
    historial_limpio = list(dict.fromkeys(historial))[-2000:]

    with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as archivo:
        json.dump(historial_limpio, archivo, ensure_ascii=False, indent=2)

    log.info(f"Historial guardado: {len(historial_limpio)} noticias")


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def limpiar_html(texto):
    if not texto:
        return ""

    soup = BeautifulSoup(texto, "html.parser")
    texto_plano = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", texto_plano).strip()


def recortar(texto, maximo, sufijo="..."):
    if not texto or len(texto) <= maximo:
        return texto
    return texto[:maximo].rstrip() + sufijo


def _parece_error_de_traduccion(texto):
    """Detecta si la respuesta del traductor es en realidad una página
    de error de Google devuelta con HTTP 200 (bug conocido de deep_translator
    ante rate-limit o bloqueo temporal)."""
    if not texto:
        return False

    texto_lower = texto.lower()
    return any(firma in texto_lower for firma in FIRMAS_ERROR_TRADUCCION)


def _dividir_en_chunks(texto, limite=LIMITE_CHARS_TRADUCTOR):
    """Divide el texto en bloques <= limite, cortando preferentemente
    en fin de oración para no romper el contexto de traducción."""
    if len(texto) <= limite:
        return [texto]

    oraciones = re.split(r"(?<=[.!?])\s+", texto)
    chunks = []
    actual = ""

    for oracion in oraciones:
        # Oración individual más larga que el límite: corte duro
        while len(oracion) > limite:
            if actual:
                chunks.append(actual)
                actual = ""
            chunks.append(oracion[:limite])
            oracion = oracion[limite:]

        if not actual:
            actual = oracion
        elif len(actual) + 1 + len(oracion) <= limite:
            actual = f"{actual} {oracion}"
        else:
            chunks.append(actual)
            actual = oracion

    if actual:
        chunks.append(actual)

    return chunks


# --- Motores de traducción -------------------------------------------------

def _motor_deep_translator(texto, source):
    """Motor 1: librería deep_translator."""
    if not DEEP_TRANSLATOR_DISPONIBLE:
        raise RuntimeError("deep_translator no disponible")

    resultado = GoogleTranslator(source=source, target=IDIOMA_DESTINO).translate(texto)

    if not resultado or not str(resultado).strip():
        raise ValueError("Respuesta vacía del traductor")

    if _parece_error_de_traduccion(resultado):
        raise ValueError("Respuesta con firma de error del servicio")

    return str(resultado)


def _motor_http_directo(texto):
    """Motor 2 (fallback): endpoint público de Google Translate.
    No depende de la versión de deep_translator ni de su parser HTML."""
    parametros = {
        "client": "gtx",
        "sl": "auto",
        "tl": IDIOMA_DESTINO,
        "dt": "t",
        "q": texto
    }

    respuesta = requests.get(
        URL_TRADUCTOR_HTTP,
        params=parametros,
        headers=HEADERS_HTTP,
        timeout=20
    )
    respuesta.raise_for_status()

    datos = respuesta.json()
    if not datos or not isinstance(datos, list) or not datos[0]:
        raise ValueError("Payload de traducción inesperado")

    partes = [fragmento[0] for fragmento in datos[0] if fragmento and fragmento[0]]
    resultado = "".join(partes).strip()

    if not resultado:
        raise ValueError("Traducción HTTP vacía")

    if _parece_error_de_traduccion(resultado):
        raise ValueError("Traducción HTTP con firma de error")

    return resultado


def _traducir_bloque(texto):
    """Traduce un bloque <= LIMITE_CHARS_TRADUCTOR probando, en orden:
    deep_translator(source='en') -> deep_translator(source='auto') -> HTTP directo.
    Cada estrategia con reintentos y backoff exponencial."""
    estrategias = [
        ("deep_translator[en]", lambda t: _motor_deep_translator(t, "en")),
        ("deep_translator[auto]", lambda t: _motor_deep_translator(t, "auto")),
        ("http_directo", _motor_http_directo),
    ]

    ultimo_error = None

    for nombre, funcion in estrategias:
        for intento in range(1, MAX_INTENTOS_TRADUCCION + 1):
            try:
                return funcion(texto)
            except Exception as error:
                ultimo_error = error
                log.warning(
                    f"Traducción fallida [{nombre}] intento "
                    f"{intento}/{MAX_INTENTOS_TRADUCCION}: {error}"
                )
                if intento < MAX_INTENTOS_TRADUCCION:
                    time.sleep(ESPERA_BASE_TRADUCCION * (2 ** (intento - 1)))

    raise RuntimeError(f"Todas las estrategias de traducción fallaron: {ultimo_error}")


def traducir(texto, ya_en_espanol=False):
    """Traduce al español respetando el límite de caracteres del servicio.
    Si todo falla, retorna el texto original (nunca HTML de error)."""
    if not texto or not texto.strip():
        return ""

    if ya_en_espanol:
        return texto

    chunks = _dividir_en_chunks(texto)
    traducidos = []

    for indice, chunk in enumerate(chunks, start=1):
        try:
            traducidos.append(_traducir_bloque(chunk))
            if len(chunks) > 1:
                time.sleep(1)  # cortesía anti rate-limit entre bloques
        except Exception as error:
            log.error(
                f"No se pudo traducir el bloque {indice}/{len(chunks)} "
                f"({len(chunk)} chars): {error}. Se usa texto original."
            )
            STATS["traducciones_fallidas"] += 1
            return texto  # consistencia: no mezclar idiomas en un mismo campo

    STATS["traducciones_ok"] += 1
    return " ".join(traducidos).strip()


def verificar_traductor():
    """Prueba de humo al arranque: permite ver en el log si el problema
    es el servicio de traducción y no el resto del pipeline."""
    if not DEEP_TRANSLATOR_DISPONIBLE:
        log.warning(
            f"deep_translator no se pudo importar ({_ERROR_IMPORT_TRADUCTOR}). "
            f"Se usará únicamente el traductor HTTP directo."
        )

    try:
        prueba = _traducir_bloque("The bass player recorded a new album.")
        log.info(f"Autotest de traducción OK -> '{prueba}'")
        return True
    except Exception as error:
        log.error(f"Autotest de traducción FALLIDO: {error}")
        return False


def es_contenido_de_bajo(titulo, resumen, fuente):
    base_exclusion = f"{titulo} {fuente}".lower()
    if APLICAR_EXCLUSIONES_A_RESUMEN:
        base_exclusion = f"{base_exclusion} {resumen}".lower()

    if any(palabra in base_exclusion for palabra in PALABRAS_EXCLUIDAS):
        return False

    if "bass" in fuente.lower():
        return True

    contenido = f"{titulo} {resumen} {fuente}".lower()
    return any(palabra in contenido for palabra in PALABRAS_CLAVE_BAJO)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def enviar_telegram(mensaje):
    """Envía un mensaje a Telegram. Retorna True solo si Telegram confirma
    la entrega (HTTP 2xx + ok:true en el payload)."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    if len(mensaje) > LIMITE_TELEGRAM:
        log.warning(
            f"Mensaje de {len(mensaje)} chars excede el límite de Telegram. Se recorta."
        )
        mensaje = mensaje[:LIMITE_TELEGRAM - 3] + "..."

    try:
        respuesta = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": mensaje,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=20
        )

        log.info(f"Telegram status: {respuesta.status_code}")

        if respuesta.status_code != 200:
            log.error(f"Telegram respondió con error: {respuesta.text}")
            return False

        payload = respuesta.json()
        if not payload.get("ok", False):
            log.error(f"Telegram ok=false: {payload}")
            return False

        return True

    except requests.exceptions.RequestException as error:
        log.error(f"Excepción enviando a Telegram: {error}")
        return False


def enviar_encabezado():
    fecha = datetime.now().strftime("%d/%m/%Y")

    mensaje = (
        "<b>BASS NEWS</b>\n"
        f"<b>Fecha:</b> {fecha}"
    )

    return enviar_telegram(mensaje)


# ---------------------------------------------------------------------------
# Obtención de feeds
# ---------------------------------------------------------------------------

def _parsear_feed(url):
    """Descarga el feed con User-Agent explícito antes de parsearlo,
    para evitar feeds vacíos por bloqueo silencioso del servidor."""
    try:
        respuesta = requests.get(url, headers=HEADERS_HTTP, timeout=15)
        respuesta.raise_for_status()
        return feedparser.parse(respuesta.content)
    except requests.exceptions.RequestException as error:
        log.warning(f"No se pudo descargar el feed ({url}): {error}")
        return feedparser.parse(url)  # fallback al método original


def obtener_noticias(historial):
    historial_set = set(historial)
    links_usados = set()
    noticias = []

    def _procesar_feed(feed, fuente, romper_en_primera):
        """feed y fuente se reciben como parámetros explícitos
        (antes feed se tomaba del scope externo)."""
        for entrada in feed.entries:
            if len(noticias) >= TOTAL_NOTICIAS:
                return

            titulo_original = limpiar_html(entrada.get("title", ""))
            resumen_original = limpiar_html(
                entrada.get("summary", entrada.get("description", ""))
            )
            link = entrada.get("link", "")

            if not titulo_original or not link:
                continue

            if link in historial_set or link in links_usados:
                continue

            if not es_contenido_de_bajo(titulo_original, resumen_original, fuente["nombre"]):
                continue

            noticias.append({
                "fuente": fuente["nombre"],
                "titulo_original": titulo_original,
                "resumen_original": resumen_original,
                "link": link
            })

            links_usados.add(link)
            log.info(f"Agregada: {titulo_original}")

            if romper_en_primera:
                return

    # Primera pasada: 1 noticia por fuente como máximo
    for fuente in FUENTES:
        if len(noticias) >= TOTAL_NOTICIAS:
            break

        log.info(f"Revisando fuente: {fuente['nombre']}")
        feed = _parsear_feed(fuente["rss"])
        _procesar_feed(feed, fuente, romper_en_primera=True)
        time.sleep(1)

    # Segunda pasada: completar cupo si faltan noticias
    if len(noticias) < TOTAL_NOTICIAS:
        log.info("Faltan noticias. Buscando adicionales...")

        for fuente in FUENTES:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            feed = _parsear_feed(fuente["rss"])
            _procesar_feed(feed, fuente, romper_en_primera=False)
            time.sleep(1)

    return noticias[:TOTAL_NOTICIAS]


# ---------------------------------------------------------------------------
# Construcción del mensaje
# ---------------------------------------------------------------------------

def crear_mensaje_noticia(noticia):
    fuente_es_espanol = noticia["fuente"].strip().lower() in FUENTES_EN_ESPANOL

    # Se recorta ANTES de traducir: evita rechazos por longitud y reduce
    # el volumen enviado al servicio.
    titulo_src = recortar(noticia["titulo_original"], MAX_LARGO_TITULO, sufijo="")
    resumen_src = recortar(noticia["resumen_original"], MAX_LARGO_RESUMEN, sufijo="")

    titulo_es = traducir(titulo_src, ya_en_espanol=fuente_es_espanol)
    resumen_es = traducir(resumen_src, ya_en_espanol=fuente_es_espanol)

    # Recorte final: el español expande ~20-25% respecto al inglés.
    titulo_es = recortar(titulo_es, MAX_LARGO_TITULO)
    resumen_es = recortar(resumen_es, MAX_LARGO_RESUMEN)

    mensaje = (
        f"<b>{html.escape(titulo_es)}</b>\n"
        f"<b>Fuente:</b> {html.escape(noticia['fuente'])}\n\n"
    )

    if resumen_es:
        mensaje += f"{html.escape(resumen_es)}\n\n"

    mensaje += f"Link: {noticia['link']}"

    return mensaje


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not TOKEN:
        log.error("Falta configurar TOKEN.")
        return

    if not CHAT_ID:
        log.error("Falta configurar CHAT_ID.")
        return

    verificar_traductor()

    historial = cargar_historial()
    noticias = obtener_noticias(historial)

    if not noticias:
        log.info("No hay noticias nuevas para enviar.")
        guardar_historial(historial)
        return

    enviar_encabezado()
    time.sleep(2)

    enviadas = 0
    fallidas = 0

    for noticia in noticias:
        mensaje = crear_mensaje_noticia(noticia)
        exito = enviar_telegram(mensaje)

        if exito:
            historial.append(noticia["link"])
            guardar_historial(historial)
            enviadas += 1
        else:
            fallidas += 1
            log.warning(
                f"No se pudo enviar (se reintentará en próxima corrida): "
                f"{noticia['titulo_original']}"
            )

        time.sleep(2)

    log.info(f"Total enviadas: {enviadas} | Total fallidas: {fallidas}")
    log.info(
        f"Traducciones OK: {STATS['traducciones_ok']} | "
        f"Traducciones fallidas: {STATS['traducciones_fallidas']}"
    )


if __name__ == "__main__":
    main()
