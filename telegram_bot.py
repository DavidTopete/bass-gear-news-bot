import os
import json
import html
import time
import logging
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from deep_translator import GoogleTranslator

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

HEADERS_RSS = {
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
    historial_limpio = list(dict.fromkeys(historial))

    with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as archivo:
        json.dump(
            historial_limpio[-2000:],
            archivo,
            ensure_ascii=False,
            indent=2
        )

    log.info(f"Historial guardado: {len(historial_limpio[-2000:])} noticias")


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def limpiar_html(texto):
    if not texto:
        return ""

    soup = BeautifulSoup(texto, "html.parser")
    return soup.get_text(" ", strip=True)


def _parece_error_de_traduccion(texto):
    """Detecta si la respuesta del traductor es en realidad una página
    de error de Google devuelta con HTTP 200 (bug conocido de deep_translator
    ante rate-limit o bloqueo temporal)."""
    if not texto:
        return False

    texto_lower = texto.lower()
    return any(firma in texto_lower for firma in FIRMAS_ERROR_TRADUCCION)


def traducir(texto, max_intentos=3, espera_base=2):
    """Traduce con reintentos y backoff exponencial. Si todos los intentos
    fallan o devuelven contenido inválido, retorna el texto original
    (nunca basura/HTML de error)."""
    if not texto:
        return ""

    for intento in range(1, max_intentos + 1):
        try:
            resultado = GoogleTranslator(source="auto", target="es").translate(texto)

            if resultado and not _parece_error_de_traduccion(resultado):
                return resultado

            log.warning(
                f"Traducción inválida (intento {intento}/{max_intentos}), "
                f"se detectó firma de error del servicio."
            )

        except Exception as error:
            log.warning(f"Fallo de traducción (intento {intento}/{max_intentos}): {error}")

        if intento < max_intentos:
            time.sleep(espera_base * (2 ** (intento - 1)))  # 2s, 4s, 8s...

    log.error("No se pudo traducir tras varios intentos. Se usa texto original.")
    return texto


def es_contenido_de_bajo(titulo, resumen, fuente):
    contenido = f"{titulo} {resumen} {fuente}".lower()

    if any(palabra in contenido for palabra in PALABRAS_EXCLUIDAS):
        return False

    if "bass" in fuente.lower():
        return True

    return any(palabra in contenido for palabra in PALABRAS_CLAVE_BAJO)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def enviar_telegram(mensaje):
    """Envía un mensaje a Telegram. Retorna True solo si Telegram confirma
    la entrega (HTTP 2xx + ok:true en el payload)."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

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

    enviar_telegram(mensaje)


# ---------------------------------------------------------------------------
# Obtención de feeds
# ---------------------------------------------------------------------------

def _parsear_feed(url):
    """Descarga el feed con User-Agent explícito antes de parsearlo,
    para evitar feeds vacíos por bloqueo silencioso del servidor."""
    try:
        respuesta = requests.get(url, headers=HEADERS_RSS, timeout=15)
        respuesta.raise_for_status()
        return feedparser.parse(respuesta.content)
    except requests.exceptions.RequestException as error:
        log.warning(f"No se pudo descargar el feed ({url}): {error}")
        return feedparser.parse(url)  # fallback al método original


def obtener_noticias(historial):
    historial_set = set(historial)
    links_usados_en_esta_corrida = set()
    noticias = []

    def _procesar_fuente(fuente, romper_en_primera):
        for entrada in feed.entries:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            titulo_original = limpiar_html(entrada.get("title", ""))
            resumen_original = limpiar_html(
                entrada.get("summary", entrada.get("description", ""))
            )
            link = entrada.get("link", "")

            if not titulo_original or not link:
                continue

            if link in historial_set:
                continue

            if link in links_usados_en_esta_corrida:
                continue

            if not es_contenido_de_bajo(titulo_original, resumen_original, fuente["nombre"]):
                continue

            noticias.append({
                "fuente": fuente["nombre"],
                "titulo_original": titulo_original,
                "resumen_original": resumen_original,
                "link": link
            })

            links_usados_en_esta_corrida.add(link)
            log.info(f"Agregada: {titulo_original}")

            if romper_en_primera:
                break

    # Primera pasada: 1 noticia por fuente como máximo
    for fuente in FUENTES:
        if len(noticias) >= TOTAL_NOTICIAS:
            break

        log.info(f"Revisando fuente: {fuente['nombre']}")
        feed = _parsear_feed(fuente["rss"])
        _procesar_fuente(fuente, romper_en_primera=True)
        time.sleep(1)

    # Segunda pasada: completar cupo si faltan noticias
    if len(noticias) < TOTAL_NOTICIAS:
        log.info("Faltan noticias. Buscando adicionales...")

        for fuente in FUENTES:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            feed = _parsear_feed(fuente["rss"])
            _procesar_fuente(fuente, romper_en_primera=False)
            time.sleep(1)

    return noticias[:TOTAL_NOTICIAS]


# ---------------------------------------------------------------------------
# Construcción del mensaje
# ---------------------------------------------------------------------------

def crear_mensaje_noticia(noticia):
    titulo_es = traducir(noticia["titulo_original"])
    resumen_es = traducir(noticia["resumen_original"])

    if len(titulo_es) > MAX_LARGO_TITULO:
        titulo_es = titulo_es[:MAX_LARGO_TITULO] + "..."

    if len(resumen_es) > MAX_LARGO_RESUMEN:
        resumen_es = resumen_es[:MAX_LARGO_RESUMEN] + "..."

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


if __name__ == "__main__":
    main()
