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

# Toda la logica de traduccion vive en traductor.py (multi-proveedor + cache).
from traductor import (
    traducir_estricto,
    TraduccionFallida,
    autotest,
    resumen_stats,
    guardar_cache,
    proveedores_agotados,
)

# ---------------------------------------------------------------------------
# Configuracion
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

LIMITE_TELEGRAM = 4096

# --- Politica de traduccion estricta ---------------------------------------
# Una noticia que no se pueda traducir NO se publica: se descarta y el bot
# sigue evaluando candidatas hasta completar TOTAL_NOTICIAS.

# Cuantas entradas se leen como maximo por cada feed al armar el pool.
MAX_ENTRADAS_POR_FUENTE = 8

# Techo duro de candidatas a evaluar. Sin esto, con el traductor caido el bot
# intentaria traducir todo el pool: coste alto y riesgo de agravar el
# rate-limit. Con 5 noticias objetivo, 40 permite ~87% de tasa de descarte.
MAX_CANDIDATOS = 40

# Circuit breaker de dos umbrales.
#
# Un solo umbral de "fallos consecutivos" es insuficiente: cada noticia exige
# DOS traducciones (titulo + resumen), asi que con 50% de fallo por campo la
# probabilidad de descartar una noticia es 1 - 0.5^2 = 75%, y seis descartes
# seguidos ocurren por azar (0.75^6 = 18%). Abortar ahi seria un falso
# positivo justo en el escenario de degradacion parcial.
#
# Regla correcta: solo se aborta pronto si NO hay evidencia de que el
# traductor funcione. En cuanto una noticia se traduce con exito, el umbral
# sube al valor "en caliente".
MAX_FALLOS_SIN_NINGUN_EXITO = 6    # traductor probablemente caido -> abortar
MAX_FALLOS_TRAS_UN_EXITO = 15      # degradacion parcial -> seguir buscando

# Si el resumen no se traduce pero el titulo si:
#   True  -> descarta la noticia completa (estricto)
#   False -> publica solo con el titulo traducido
EXIGIR_RESUMEN_TRADUCIDO = True

# Si el autotest de traduccion falla al arranque, no tiene sentido recorrer
# los feeds: en modo estricto no se publicaria nada.
ABORTAR_SI_NO_HAY_TRADUCTOR = True

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

# Fuentes que ya publican en espanol: no pasan por el traductor y por tanto
# nunca se descartan por fallo de traduccion.
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

APLICAR_EXCLUSIONES_A_RESUMEN = False


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
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    if len(mensaje) > LIMITE_TELEGRAM:
        log.warning(f"Mensaje de {len(mensaje)} chars excede el limite. Se recorta.")
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
            log.error(f"Telegram respondio con error: {respuesta.text}")
            return False

        payload = respuesta.json()
        if not payload.get("ok", False):
            log.error(f"Telegram ok=false: {payload}")
            return False

        return True

    except requests.exceptions.RequestException as error:
        log.error(f"Excepcion enviando a Telegram: {error}")
        return False


def enviar_encabezado():
    fecha = datetime.now().strftime("%d/%m/%Y")
    mensaje = (
        "<b>BASS NEWS</b>\n"
        f"<b>Fecha:</b> {fecha}"
    )
    return enviar_telegram(mensaje)


# ---------------------------------------------------------------------------
# Recoleccion de candidatas
# ---------------------------------------------------------------------------

def _parsear_feed(url):
    try:
        respuesta = requests.get(url, headers=HEADERS_HTTP, timeout=15)
        respuesta.raise_for_status()
        return feedparser.parse(respuesta.content)
    except requests.exceptions.RequestException as error:
        log.warning(f"No se pudo descargar el feed ({url}): {error}")
        return feedparser.parse(url)


def recolectar_candidatos(historial):
    """Construye el pool de candidatas SIN traducir.

    Se recorren todas las fuentes y luego se intercalan en round-robin:
    primero la entrada #1 de cada fuente, luego la #2, etc. Asi se preserva
    la prioridad de variedad del diseno original (1 por fuente) incluso
    cuando hay que profundizar para reemplazar descartes.
    """
    historial_set = set(historial)
    links_vistos = set()
    por_fuente = []

    for fuente in FUENTES:
        log.info(f"Revisando fuente: {fuente['nombre']}")
        feed = _parsear_feed(fuente["rss"])
        entradas = []

        for entrada in feed.entries[:MAX_ENTRADAS_POR_FUENTE]:
            titulo = limpiar_html(entrada.get("title", ""))
            resumen = limpiar_html(
                entrada.get("summary", entrada.get("description", ""))
            )
            link = entrada.get("link", "")

            if not titulo or not link:
                continue
            if link in historial_set or link in links_vistos:
                continue
            if not es_contenido_de_bajo(titulo, resumen, fuente["nombre"]):
                continue

            entradas.append({
                "fuente": fuente["nombre"],
                "titulo_original": titulo,
                "resumen_original": resumen,
                "link": link
            })
            links_vistos.add(link)

        log.info(f"  -> {len(entradas)} candidatas de {fuente['nombre']}")
        por_fuente.append(entradas)
        time.sleep(1)

    # Intercalado round-robin
    candidatos = []
    for nivel in range(MAX_ENTRADAS_POR_FUENTE):
        for entradas in por_fuente:
            if nivel < len(entradas):
                candidatos.append(entradas[nivel])

    log.info(f"Pool total de candidatas: {len(candidatos)}")
    return candidatos[:MAX_CANDIDATOS]


# ---------------------------------------------------------------------------
# Preparacion (traduccion) y seleccion
# ---------------------------------------------------------------------------

def preparar_noticia(noticia):
    """Traduce y arma el mensaje. Devuelve None si la noticia no es
    publicable por fallo de traduccion."""
    fuente_en_espanol = noticia["fuente"].strip().lower() in FUENTES_EN_ESPANOL

    # Recorte ANTES de traducir: menos cuota consumida.
    titulo_src = recortar(noticia["titulo_original"], MAX_LARGO_TITULO, sufijo="")
    resumen_src = recortar(noticia["resumen_original"], MAX_LARGO_RESUMEN, sufijo="")

    if fuente_en_espanol:
        titulo_final, resumen_final = titulo_src, resumen_src
    else:
        try:
            titulo_final = traducir_estricto(titulo_src)
        except TraduccionFallida as error:
            log.warning(f"DESCARTADA (titulo sin traducir): {titulo_src[:70]} | {error}")
            return None

        if not resumen_src:
            resumen_final = ""
        else:
            try:
                resumen_final = traducir_estricto(resumen_src)
            except TraduccionFallida as error:
                if EXIGIR_RESUMEN_TRADUCIDO:
                    log.warning(
                        f"DESCARTADA (resumen sin traducir): {titulo_src[:70]} | {error}"
                    )
                    return None
                log.warning("Resumen sin traducir; se publica solo el titulo.")
                resumen_final = ""

    titulo_final = recortar(titulo_final, MAX_LARGO_TITULO)
    resumen_final = recortar(resumen_final, MAX_LARGO_RESUMEN)

    mensaje = (
        f"<b>{html.escape(titulo_final)}</b>\n"
        f"<b>Fuente:</b> {html.escape(noticia['fuente'])}\n\n"
    )
    if resumen_final:
        mensaje += f"{html.escape(resumen_final)}\n\n"
    mensaje += f"Link: {noticia['link']}"

    return {
        "link": noticia["link"],
        "fuente": noticia["fuente"],
        "titulo_original": noticia["titulo_original"],
        "mensaje": mensaje
    }


def seleccionar_lote(candidatos):
    """Recorre las candidatas traduciendo una por una y quedandose solo con
    las publicables, hasta completar TOTAL_NOTICIAS."""
    lote = []
    evaluadas = 0
    descartadas = 0
    fallos_consecutivos = 0

    for noticia in candidatos:
        if len(lote) >= TOTAL_NOTICIAS:
            break

        # Umbral dinamico: estricto mientras no haya ninguna prueba de que
        # el traductor responde; tolerante una vez que la hay.
        umbral = MAX_FALLOS_TRAS_UN_EXITO if lote else MAX_FALLOS_SIN_NINGUN_EXITO

        if fallos_consecutivos >= umbral:
            if not lote:
                log.error(
                    f"Circuit breaker: {fallos_consecutivos} fallos consecutivos "
                    f"sin ninguna traduccion exitosa. El traductor esta caido; "
                    f"se detiene la busqueda."
                )
            else:
                log.error(
                    f"Circuit breaker: {fallos_consecutivos} fallos consecutivos "
                    f"tras {len(lote)} exitos. El proveedor se degrado a mitad "
                    f"de la corrida; se publica lo que hay."
                )
            break

        if proveedores_agotados():
            log.error("Todos los proveedores de traduccion quedaron agotados.")
            break

        evaluadas += 1
        preparada = preparar_noticia(noticia)

        if preparada is None:
            descartadas += 1
            fallos_consecutivos += 1
            continue

        fallos_consecutivos = 0
        lote.append(preparada)
        log.info(f"[{len(lote)}/{TOTAL_NOTICIAS}] Lista: {preparada['titulo_original'][:70]}")

    log.info(
        f"Seleccion terminada: {len(lote)} publicables | "
        f"{evaluadas} evaluadas | {descartadas} descartadas por traduccion"
    )

    if len(lote) < TOTAL_NOTICIAS:
        log.warning(
            f"Solo se completaron {len(lote)}/{TOTAL_NOTICIAS}. "
            f"Pool de candidatas agotado o traductor degradado. "
            f"Las descartadas NO se marcan en el historial y se reintentaran."
        )

    return lote


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

    proveedor = autotest()
    if proveedor is None and ABORTAR_SI_NO_HAY_TRADUCTOR:
        log.error(
            "Sin traductor disponible. En modo estricto no se publicaria nada; "
            "se aborta la corrida sin tocar el historial."
        )
        return

    historial = cargar_historial()

    candidatos = recolectar_candidatos(historial)
    if not candidatos:
        log.info("No hay noticias nuevas que evaluar.")
        guardar_historial(historial)
        return

    lote = seleccionar_lote(candidatos)
    guardar_cache()

    if not lote:
        log.warning("Ninguna noticia resulto publicable. No se envia nada.")
        guardar_historial(historial)
        log.info(resumen_stats())
        return

    enviar_encabezado()
    time.sleep(2)

    enviadas = 0
    fallidas = 0

    for noticia in lote:
        if enviar_telegram(noticia["mensaje"]):
            historial.append(noticia["link"])
            guardar_historial(historial)
            enviadas += 1
        else:
            fallidas += 1
            log.warning(
                f"No se pudo enviar (se reintentara en proxima corrida): "
                f"{noticia['titulo_original']}"
            )
        time.sleep(2)

    log.info(f"Total enviadas: {enviadas} | Total fallidas: {fallidas}")
    log.info(resumen_stats())


if __name__ == "__main__":
    main()
