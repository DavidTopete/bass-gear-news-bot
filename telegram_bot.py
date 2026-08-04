import os
import json
import html
import time
import requests
import feedparser

from bs4 import BeautifulSoup
from datetime import datetime
from deep_translator import GoogleTranslator


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ARCHIVO_HISTORIAL = "noticias_enviadas_bass.json"

TOTAL_NOTICIAS = 5
MAXIMO_HISTORIAL = 2000
MAXIMO_RESUMEN = 750

TIEMPO_ESPERA_ENTRE_FUENTES = 1
TIEMPO_ESPERA_ENTRE_MENSAJES = 2
TIMEOUT_HTTP = 20


# ============================================================
# FUENTES RSS
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
        "nombre": "For Bass Players Only",
        "rss": "https://forbassplayersonly.com/feed/"
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
# FILTROS
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
    "bass pedal",
    "bass player",
    "bajo",
    "bajista",
    "contrabajo"
]

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

PALABRAS_ERROR_SERVIDOR = [
    "error 400",
    "error 401",
    "error 403",
    "error 404",
    "error 500",
    "error 502",
    "error 503",
    "error 504",
    "server error",
    "internal server error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "please try again later",
    "that's an error",
    "that’s an error",
    "there was an error",
    "access denied",
    "forbidden",
    "page not found",
    "not found",
    "temporarily unavailable",
    "cloudflare",
    "captcha"
]


# ============================================================
# SESIÓN HTTP
# ============================================================

SESION = requests.Session()

SESION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36 BassNewsBot/1.0"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, "
        "application/xml, text/xml, */*"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8"
})


# ============================================================
# HISTORIAL
# ============================================================

def cargar_historial():
    """Carga los enlaces enviados anteriormente."""

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
            print("El historial no contiene una lista. Se reiniciará.")
            historial = []

        historial = [
            enlace
            for enlace in historial
            if isinstance(enlace, str) and enlace.strip()
        ]

        print(
            f"Historial cargado: "
            f"{len(historial)} noticias registradas"
        )

        return historial

    except (OSError, json.JSONDecodeError) as error:
        print(
            "Error leyendo historial. "
            "Se reiniciará el archivo:",
            error
        )

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
        except OSError as error_escritura:
            print(
                "No se pudo reiniciar el historial:",
                error_escritura
            )

        return []


def guardar_historial(historial):
    """Guarda los enlaces sin duplicados."""

    historial_limpio = []

    for enlace in historial:
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
# LIMPIEZA Y TRADUCCIÓN
# ============================================================

def limpiar_html(texto):
    """Elimina etiquetas HTML y espacios innecesarios."""

    if not texto:
        return ""

    try:
        soup = BeautifulSoup(str(texto), "html.parser")
        texto_limpio = soup.get_text(" ", strip=True)

        return " ".join(texto_limpio.split())

    except Exception:
        return str(texto).strip()


def traducir(texto):
    """Traduce texto al español y devuelve el original si falla."""

    if not texto:
        return ""

    try:
        traduccion = GoogleTranslator(
            source="auto",
            target="es"
        ).translate(texto)

        return traduccion if traduccion else texto

    except Exception as error:
        print("No se pudo traducir el texto:", error)
        return texto


# ============================================================
# VALIDACIÓN DEL CONTENIDO
# ============================================================

def contiene_error_servidor(titulo, resumen):
    """Detecta páginas de error enviadas como entradas RSS."""

    contenido = f"{titulo} {resumen}".lower()

    return any(
        palabra in contenido
        for palabra in PALABRAS_ERROR_SERVIDOR
    )


def es_contenido_de_bajo(titulo, resumen, fuente):
    """Determina si la entrada está relacionada con el bajo."""

    titulo = titulo or ""
    resumen = resumen or ""
    fuente = fuente or ""

    contenido_noticia = f"{titulo} {resumen}".lower()
    contenido_completo = (
        f"{titulo} {resumen} {fuente}"
    ).lower()

    if contiene_error_servidor(titulo, resumen):
        print(f"Página de error descartada: {titulo}")
        return False

    if any(
        palabra in contenido_noticia
        for palabra in PALABRAS_EXCLUIDAS
    ):
        print(f"Contenido excluido: {titulo}")
        return False

    # En fuentes cuyo nombre está dedicado específicamente al bajo,
    # se acepta la noticia siempre que no sea una página de error.
    if "bass" in fuente.lower():
        return True

    return any(
        palabra in contenido_completo
        for palabra in PALABRAS_CLAVE_BAJO
    )


# ============================================================
# DESCARGA DE RSS
# ============================================================

def obtener_feed(fuente):
    """
    Descarga y analiza una fuente RSS.
    Devuelve None cuando la fuente tiene errores.
    """

    nombre = fuente["nombre"]
    url_rss = fuente["rss"]

    try:
        respuesta = SESION.get(
            url_rss,
            timeout=TIMEOUT_HTTP,
            allow_redirects=True
        )

        print(
            f"HTTP {respuesta.status_code} - "
            f"{nombre} - {respuesta.url}"
        )

        if respuesta.status_code != 200:
            print(
                f"Fuente omitida: {nombre}. "
                f"El servidor respondió HTTP "
                f"{respuesta.status_code}."
            )
            return None

        if not respuesta.content:
            print(f"Fuente vacía: {nombre}")
            return None

        texto_inicial = respuesta.text[:2000].lower()

        if any(
            palabra in texto_inicial
            for palabra in PALABRAS_ERROR_SERVIDOR
        ):
            print(
                f"La respuesta de {nombre} parece ser "
                f"una página de error."
            )
            return None

        feed = feedparser.parse(respuesta.content)

        if getattr(feed, "bozo", 0):
            print(
                f"Advertencia al analizar {nombre}: "
                f"{getattr(feed, 'bozo_exception', 'RSS inválido')}"
            )

        if not getattr(feed, "entries", None):
            print(f"Sin entradas RSS válidas: {nombre}")
            return None

        return feed

    except requests.Timeout:
        print(
            f"Tiempo de espera agotado al descargar: "
            f"{nombre}"
        )
        return None

    except requests.RequestException as error:
        print(
            f"Error de conexión al descargar {nombre}: "
            f"{error}"
        )
        return None

    except Exception as error:
        print(
            f"Error inesperado leyendo {nombre}: "
            f"{error}"
        )
        return None


# ============================================================
# EXTRACCIÓN DE ENTRADAS
# ============================================================

def obtener_datos_entrada(entrada):
    """Extrae título, resumen y enlace de una entrada RSS."""

    titulo_original = limpiar_html(
        entrada.get("title", "")
    )

    resumen_original = limpiar_html(
        entrada.get(
            "summary",
            entrada.get(
                "description",
                entrada.get("content", "")
            )
        )
    )

    # Algunos feeds almacenan content como lista.
    if isinstance(
        entrada.get("content"),
        list
    ) and not resumen_original:
        contenidos = entrada.get("content", [])

        if contenidos:
            resumen_original = limpiar_html(
                contenidos[0].get("value", "")
            )

    link = entrada.get("link", "").strip()

    if not link:
        enlaces = entrada.get("links", [])

        for enlace in enlaces:
            if enlace.get("rel") == "alternate":
                link = enlace.get("href", "").strip()
                break

    return titulo_original, resumen_original, link


def entrada_es_valida(
    titulo,
    resumen,
    link,
    fuente,
    historial_set,
    links_corrida
):
    """Valida que una entrada pueda agregarse."""

    if not titulo:
        return False

    if not link:
        return False

    if not link.startswith(("http://", "https://")):
        print(f"Enlace inválido ignorado: {link}")
        return False

    if link in historial_set:
        print(f"Repetida ignorada: {titulo}")
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
    """Obtiene noticias nuevas de todas las fuentes."""

    historial_set = set(historial)
    links_usados_en_esta_corrida = set()
    noticias = []

    feeds_descargados = {}

    # Primera ronda:
    # intenta tomar una noticia de cada fuente.
    for fuente in FUENTES:
        if len(noticias) >= TOTAL_NOTICIAS:
            break

        nombre_fuente = fuente["nombre"]

        print("\n" + "=" * 60)
        print(f"Revisando fuente: {nombre_fuente}")

        feed = obtener_feed(fuente)
        feeds_descargados[nombre_fuente] = feed

        if feed is None:
            continue

        for entrada in feed.entries:
            (
                titulo_original,
                resumen_original,
                link
            ) = obtener_datos_entrada(entrada)

            if not entrada_es_valida(
                titulo=titulo_original,
                resumen=resumen_original,
                link=link,
                fuente=nombre_fuente,
                historial_set=historial_set,
                links_corrida=links_usados_en_esta_corrida
            ):
                continue

            noticias.append({
                "fuente": nombre_fuente,
                "titulo_original": titulo_original,
                "resumen_original": resumen_original,
                "link": link
            })

            links_usados_en_esta_corrida.add(link)

            print(f"Agregada: {titulo_original}")

            # Solo una noticia por fuente en la primera ronda.
            break

        time.sleep(TIEMPO_ESPERA_ENTRE_FUENTES)

    # Segunda ronda:
    # si faltan noticias, toma más entradas de los feeds válidos.
    if len(noticias) < TOTAL_NOTICIAS:
        print("\nFaltan noticias. Buscando adicionales...")

        for fuente in FUENTES:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            nombre_fuente = fuente["nombre"]
            feed = feeds_descargados.get(nombre_fuente)

            if feed is None:
                continue

            for entrada in feed.entries:
                if len(noticias) >= TOTAL_NOTICIAS:
                    break

                (
                    titulo_original,
                    resumen_original,
                    link
                ) = obtener_datos_entrada(entrada)

                if not entrada_es_valida(
                    titulo=titulo_original,
                    resumen=resumen_original,
                    link=link,
                    fuente=nombre_fuente,
                    historial_set=historial_set,
                    links_corrida=links_usados_en_esta_corrida
                ):
                    continue

                noticias.append({
                    "fuente": nombre_fuente,
                    "titulo_original": titulo_original,
                    "resumen_original": resumen_original,
                    "link": link
                })

                links_usados_en_esta_corrida.add(link)

                print(
                    f"Agregada adicional: "
                    f"{titulo_original}"
                )

            time.sleep(TIEMPO_ESPERA_ENTRE_FUENTES)

    return noticias[:TOTAL_NOTICIAS]


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(mensaje):
    """
    Envía un mensaje a Telegram.
    Devuelve True cuando Telegram confirma el envío.
    """

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
            f"Telegram HTTP status: "
            f"{respuesta.status_code}"
        )

        try:
            resultado = respuesta.json()
        except ValueError:
            print(
                "Telegram devolvió una respuesta "
                "que no es JSON:"
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

        print("Mensaje enviado correctamente a Telegram.")
        return True

    except requests.Timeout:
        print(
            "Tiempo de espera agotado al enviar "
            "el mensaje a Telegram."
        )
        return False

    except requests.RequestException as error:
        print(
            "Error de conexión con Telegram:",
            error
        )
        return False

    except Exception as error:
        print(
            "Error inesperado enviando a Telegram:",
            error
        )
        return False


def enviar_encabezado():
    """Envía el encabezado diario."""

    fecha = datetime.now().strftime("%d/%m/%Y")

    mensaje = (
        "<b>🎸 BASS NEWS</b>\n"
        f"<b>Fecha:</b> {html.escape(fecha)}"
    )

    return enviar_telegram(mensaje)


# ============================================================
# CREACIÓN DEL MENSAJE
# ============================================================

def crear_mensaje_noticia(noticia):
    """Traduce y crea el mensaje HTML para Telegram."""

    titulo_original = noticia.get(
        "titulo_original",
        ""
    )

    resumen_original = noticia.get(
        "resumen_original",
        ""
    )

    fuente = noticia.get("fuente", "")
    link = noticia.get("link", "")

    titulo_es = traducir(titulo_original)
    resumen_es = traducir(resumen_original)

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
        mensaje += f"{resumen_seguro}\n\n"

    mensaje += (
        f'<a href="{link_seguro}">'
        f"Leer noticia completa"
        f"</a>"
    )

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
            "Falta configurar la variable "
            "de entorno TOKEN."
        )
        return

    if not CHAT_ID:
        print(
            "Falta configurar la variable "
            "de entorno CHAT_ID."
        )
        return

    historial = cargar_historial()
    noticias = obtener_noticias(historial)

    print("\n" + "=" * 60)
    print(
        f"Noticias nuevas encontradas: "
        f"{len(noticias)}"
    )
    print("=" * 60)

    if not noticias:
        print("No hay noticias nuevas para enviar.")
        guardar_historial(historial)
        return

    encabezado_enviado = enviar_encabezado()

    if not encabezado_enviado:
        print(
            "No se pudo enviar el encabezado. "
            "Se cancela el envío de noticias."
        )
        return

    time.sleep(TIEMPO_ESPERA_ENTRE_MENSAJES)

    total_enviadas = 0
    total_fallidas = 0

    for numero, noticia in enumerate(
        noticias,
        start=1
    ):
        print("\n" + "-" * 60)
        print(
            f"Preparando noticia "
            f"{numero}/{len(noticias)}"
        )
        print(
            f"Título: "
            f"{noticia['titulo_original']}"
        )

        mensaje = crear_mensaje_noticia(noticia)

        enviado = enviar_telegram(mensaje)

        if enviado:
            historial.append(noticia["link"])
            guardar_historial(historial)

            total_enviadas += 1

        else:
            print(
                "La noticia no se agregó al historial "
                "porque no pudo enviarse."
            )

            total_fallidas += 1

        time.sleep(TIEMPO_ESPERA_ENTRE_MENSAJES)

    print("\n" + "=" * 60)
    print("PROCESO TERMINADO")
    print(f"Total encontradas: {len(noticias)}")
    print(f"Total enviadas: {total_enviadas}")
    print(f"Total fallidas: {total_fallidas}")
    print("=" * 60)


if __name__ == "__main__":
    main()
