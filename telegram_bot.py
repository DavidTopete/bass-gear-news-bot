import os
import json
import html
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from deep_translator import GoogleTranslator

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ARCHIVO_HISTORIAL = "noticias_enviadas_bass.json"
TOTAL_NOTICIAS = 10

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


def cargar_historial():
    if not os.path.exists(ARCHIVO_HISTORIAL):
        print("No existe historial. Creando archivo nuevo...")
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as archivo:
            json.dump([], archivo, ensure_ascii=False, indent=2)
        return []

    try:
        with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as archivo:
            historial = json.load(archivo)

        if not isinstance(historial, list):
            historial = []

        print(f"Historial cargado: {len(historial)} noticias registradas")
        return historial

    except Exception as error:
        print("Error leyendo historial. Reiniciando archivo:", error)
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

    print(f"Historial guardado: {len(historial_limpio[-2000:])} noticias")


def limpiar_html(texto):
    if not texto:
        return ""

    soup = BeautifulSoup(texto, "html.parser")
    return soup.get_text(" ", strip=True)


def traducir(texto):
    if not texto:
        return ""

    try:
        return GoogleTranslator(source="auto", target="es").translate(texto)
    except Exception:
        return texto


def es_contenido_de_bajo(titulo, resumen, fuente):
    contenido = f"{titulo} {resumen} {fuente}".lower()

    if any(palabra in contenido for palabra in PALABRAS_EXCLUIDAS):
        return False

    if "bass" in fuente.lower():
        return True

    return any(palabra in contenido for palabra in PALABRAS_CLAVE_BAJO)


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

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

    print("Telegram status:", respuesta.status_code)
    print(respuesta.text)


def enviar_encabezado():
    fecha = datetime.now().strftime("%d/%m/%Y")

    mensaje = (
        "<b>BASS NEWS</b>\n"
        f"<b>Fecha:</b> {fecha}"
    )

    enviar_telegram(mensaje)


def obtener_noticias(historial):
    links_usados_en_esta_corrida = set()
    noticias = []

    for fuente in FUENTES:
        if len(noticias) >= TOTAL_NOTICIAS:
            break

        print(f"Revisando fuente: {fuente['nombre']}")

        try:
            feed = feedparser.parse(fuente["rss"])
        except Exception as error:
            print(f"Error leyendo {fuente['nombre']}: {error}")
            continue

        for entrada in feed.entries:
            titulo_original = limpiar_html(entrada.get("title", ""))
            resumen_original = limpiar_html(
                entrada.get("summary", entrada.get("description", ""))
            )
            link = entrada.get("link", "")

            if not titulo_original or not link:
                continue

            if link in historial:
                print(f"Repetida ignorada: {titulo_original}")
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
            print(f"Agregada: {titulo_original}")
            break

        time.sleep(1)

    if len(noticias) < TOTAL_NOTICIAS:
        print("Faltan noticias. Buscando adicionales...")

        for fuente in FUENTES:
            if len(noticias) >= TOTAL_NOTICIAS:
                break

            try:
                feed = feedparser.parse(fuente["rss"])
            except Exception:
                continue

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

                if link in historial:
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
                print(f"Agregada adicional: {titulo_original}")

            time.sleep(1)

    return noticias[:TOTAL_NOTICIAS]


def crear_mensaje_noticia(noticia):
    titulo_es = traducir(noticia["titulo_original"])
    resumen_es = traducir(noticia["resumen_original"])

    if len(resumen_es) > 750:
        resumen_es = resumen_es[:750] + "..."

    mensaje = (
        f"<b>{html.escape(titulo_es)}</b>\n"
        f"<b>Fuente:</b> {html.escape(noticia['fuente'])}\n\n"
    )

    if resumen_es:
        mensaje += f"{html.escape(resumen_es)}\n\n"

    mensaje += f"Link: {noticia['link']}"

    return mensaje


def main():
    if not TOKEN:
        print("Falta configurar TOKEN.")
        return

    if not CHAT_ID:
        print("Falta configurar CHAT_ID.")
        return

    historial = cargar_historial()
    noticias = obtener_noticias(historial)

    if not noticias:
        print("No hay noticias nuevas para enviar.")
        guardar_historial(historial)
        return

    enviar_encabezado()
    time.sleep(2)

    for noticia in noticias:
        mensaje = crear_mensaje_noticia(noticia)
        enviar_telegram(mensaje)

        historial.append(noticia["link"])

        guardar_historial(historial)

        time.sleep(2)

    print(f"Total enviadas: {len(noticias)}")


if __name__ == "__main__":
    main()
