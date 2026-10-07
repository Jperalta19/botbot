import asyncio
from contextlib import ExitStack
import itertools
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import instaloader
import yt_dlp
from dotenv import load_dotenv
from telegram import BotCommand, InputMediaPhoto, InputMediaVideo, Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, ExtBot, MessageHandler, filters


load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("media_bot")

MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "49"))
MAX_VIDEO_BYTES = MAX_VIDEO_MB * 1024 * 1024
MAX_MEDIA_ITEMS = int(os.getenv("MAX_MEDIA_ITEMS", "20"))
MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024
INSTAGRAM_POST_PATH = re.compile(r"^/(?:p|reel|reels|tv)/(?P<code>[^/]+)/?$")
INSTAGRAM_STORY_PATH = re.compile(r"^/stories/(?P<user>[A-Za-z0-9._]+)/(?P<id>\d+)/?$")
INSTAGRAM_HIGHLIGHT_PATH = re.compile(r"^/stories/highlights/(?P<id>\d+)/?$")
DEFAULT_DOMAINS = {
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "facebook.com",
    "fb.watch",
    "x.com",
    "twitter.com",
    "reddit.com",
    "redd.it",
    "dailymotion.com",
    "dai.ly",
    "soundcloud.com",
    "pinterest.com",
    "pin.it",
    "threads.net",
    "snapchat.com",
}
ALLOWED_DOMAINS = {
    domain.strip().lower()
    for domain in os.getenv("ALLOWED_DOMAINS", ",".join(DEFAULT_DOMAINS)).split(",")
    if domain.strip()
}


class UnsupportedURL(ValueError):
    pass


class VideoTooLarge(ValueError):
    pass


class InstagramSessionRequired(ValueError):
    pass


class TooManyMedia(ValueError):
    pass


class CobaltError(ValueError):
    pass


class ReconnectingBot(ExtBot):
    async def get_updates(self, *args: Any, **kwargs: Any) -> tuple[Update, ...]:
        while True:
            try:
                return await super().get_updates(*args, **kwargs)
            except NetworkError as error:
                logger.warning("Se perdió la conexión con Telegram: %s. Reintento en 5 segundos.", error)
                await asyncio.sleep(5)


def extract_url(text: str) -> str:
    match = re.search(r"https?://\S+", text)
    if not match:
        raise UnsupportedURL("Envía un enlace HTTPS de un video público.")
    return match.group(0).rstrip(".,!?)]}>")


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not hostname:
        raise UnsupportedURL("Solo se aceptan enlaces HTTPS.")
    if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in ALLOWED_DOMAINS):
        raise UnsupportedURL("Ese sitio no está habilitado en este bot.")
    return url


def progress_bar(percent: float, width: int = 12) -> str:
    filled = round(width * max(0, min(percent, 100)) / 100)
    return f"[{'#' * filled}{'.' * (width - filled)}] {percent:.0f}%"


def classify_instagram_url(url: str) -> tuple[str, re.Match[str]] | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower().removeprefix("www.") != "instagram.com":
        return None
    for kind, pattern in (
        ("highlight", INSTAGRAM_HIGHLIGHT_PATH),
        ("story", INSTAGRAM_STORY_PATH),
        ("post", INSTAGRAM_POST_PATH),
    ):
        match = pattern.fullmatch(parts.path)
        if match:
            return kind, match
    return None


def download_video(url: str, directory: str, state: dict[str, object]) -> Path:
    def progress_hook(info: dict[str, object]) -> None:
        downloaded = int(info.get("downloaded_bytes") or 0)
        if downloaded > MAX_VIDEO_BYTES:
            raise VideoTooLarge(f"El video supera el límite de {MAX_VIDEO_MB} MB.")
        state["downloaded"] = downloaded
        total = info.get("total_bytes") or info.get("total_bytes_estimate")
        state["percent"] = downloaded * 100 / int(total) if total else None

    options = {
        "format": "best[ext=mp4][height<=1080]/best[height<=1080]/best",
        "outtmpl": str(Path(directory) / "%(title).80B-%(id).40B.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_VIDEO_BYTES,
        "socket_timeout": 30,
        "retries": 2,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    media_files = [
        path
        for path in Path(directory).iterdir()
        if path.is_file() and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]
    if not media_files:
        raise yt_dlp.utils.DownloadError("No se encontró un archivo de video descargado.")
    media_path = media_files[0]
    if media_path.stat().st_size > MAX_VIDEO_BYTES:
        raise VideoTooLarge(f"El video supera el límite de {MAX_VIDEO_MB} MB.")
    return media_path


def download_snapchat(url: str, directory: str, state: dict[str, object]) -> Path:
    api_url = os.getenv("COBALT_API_URL", "").strip()
    api_parts = urlsplit(api_url)
    if api_parts.scheme != "https" or not api_parts.hostname:
        raise CobaltError("Configura COBALT_API_URL con una instancia Cobalt autorizada para descargar Snapchat.")

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key = os.getenv("COBALT_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"
    request = Request(
        f"{api_url.rstrip('/')}/",
        data=json.dumps({"url": url, "videoQuality": "1080", "filenameStyle": "basic"}).encode(),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("Falló la solicitud a Cobalt: %s", error)
        raise CobaltError("No pude comunicarme con la instancia Cobalt configurada.") from error

    if result.get("status") == "error":
        raise CobaltError("La instancia Cobalt rechazó el enlace de Snapchat.")
    if result.get("status") not in {"tunnel", "redirect"} or not result.get("url"):
        raise CobaltError("La instancia Cobalt devolvió una respuesta no compatible para este enlace.")

    download_url = result["url"]
    if urlsplit(download_url).scheme != "https":
        raise CobaltError("Cobalt devolvió un enlace de descarga no seguro.")
    filename = Path(str(result.get("filename") or "snapchat-video.mp4")).name
    if not filename or filename in {".", ".."}:
        filename = "snapchat-video.mp4"
    media_path = Path(directory) / filename

    try:
        with urlopen(download_url, timeout=30) as response, media_path.open("wb") as media_file:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_VIDEO_BYTES:
                raise VideoTooLarge(f"El video supera el límite de {MAX_VIDEO_MB} MB.")
            downloaded = 0
            while chunk := response.read(64 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_VIDEO_BYTES:
                    raise VideoTooLarge(f"El video supera el límite de {MAX_VIDEO_MB} MB.")
                media_file.write(chunk)
                state["downloaded"] = downloaded
                state["percent"] = downloaded * 100 / total if total else None
    except (HTTPError, URLError, TimeoutError) as error:
        logger.warning("Falló la descarga del archivo de Cobalt: %s", error)
        raise CobaltError("No pude descargar el archivo devuelto por Cobalt.") from error

    return media_path


def download_instagram(
    url: str,
    directory: str,
    state: dict[str, object],
    highlight_owner: str | None = None,
) -> list[Path]:
    classified = classify_instagram_url(url)
    if classified is None:
        raise ValueError("No reconozco el tipo de enlace de Instagram.")
    kind, match = classified
    session_file = os.getenv("INSTAGRAM_SESSION_FILE")
    session_user = os.getenv("INSTAGRAM_USERNAME")
    if bool(session_file) != bool(session_user):
        raise InstagramSessionRequired(
            "Configura juntos INSTAGRAM_USERNAME e INSTAGRAM_SESSION_FILE para usar historias o destacados."
        )
    if kind in {"story", "highlight"} and not session_file:
        raise InstagramSessionRequired(
            "Instagram exige una sesión válida para descargar historias y destacados."
        )

    loader = instaloader.Instaloader(
        sleep=False,
        quiet=True,
        dirname_pattern=directory,
        filename_pattern="{shortcode}_{filename}",
        download_video_thumbnails=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        storyitem_metadata_txt_pattern="",
    )
    if session_file and session_user:
        loader.load_session_from_file(session_user, session_file)
        if not loader.test_login():
            raise InstagramSessionRequired("La sesión guardada de Instagram ya no es válida.")

    state["percent"] = None
    if kind == "post":
        post = instaloader.Post.from_shortcode(loader.context, match.group("code"))
        loader.download_post(post, target="media")
    else:
        username = match.group("user") if kind == "story" else highlight_owner
        if not username:
            raise InstagramSessionRequired(
                "Para un destacado, envía /highlight usuario URL_DEL_DESTACADO."
            )
        profile = instaloader.Profile.from_username(loader.context, username.lstrip("@"))
        if kind == "story":
            story_id = match.group("id")
            items = (
                item
                for story in loader.get_stories(userids=[profile.userid])
                for item in story.get_items()
                if str(item.mediaid) == story_id
            )
            item = next(items, None)
            if item is None:
                raise ValueError("No encontré esa historia entre las historias visibles para la sesión.")
            loader.download_storyitem(item, target="media")
        else:
            highlight_id = match.group("id")
            highlight = next(
                (item for item in loader.get_highlights(profile) if str(item.unique_id) == highlight_id),
                None,
            )
            if highlight is None:
                raise ValueError("No encontré ese destacado en el perfil indicado.")
            for index, item in enumerate(highlight.get_items()):
                if index >= MAX_MEDIA_ITEMS:
                    raise TooManyMedia(f"El destacado supera el límite de {MAX_MEDIA_ITEMS} elementos.")
                loader.download_storyitem(item, target="media")

    media_paths = sorted(
        path
        for path in Path(directory).iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".mp4"}
    )
    if not media_paths:
        raise yt_dlp.utils.DownloadError("Instagram no devolvió archivos multimedia.")
    if len(media_paths) > MAX_MEDIA_ITEMS:
        raise TooManyMedia(f"La publicación supera el límite de {MAX_MEDIA_ITEMS} elementos.")
    for media_path in media_paths:
        if media_path.stat().st_size > MAX_VIDEO_BYTES:
            raise VideoTooLarge(f"Un archivo supera el límite de {MAX_VIDEO_MB} MB.")
    return media_paths


async def send_media_files(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    media_paths: list[Path],
    state: dict[str, object],
) -> None:
    batches: list[list[Path]] = []
    batch: list[Path] = []
    for media_path in media_paths:
        is_large_photo = (
            media_path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            and media_path.stat().st_size > MAX_TELEGRAM_PHOTO_BYTES
        )
        if is_large_photo:
            if batch:
                batches.append(batch)
                batch = []
            batches.append([media_path])
        else:
            batch.append(media_path)
            if len(batch) == 10:
                batches.append(batch)
                batch = []
    if batch:
        batches.append(batch)

    sent_count = 0
    for current_batch in batches:
        state["upload_status"] = f"{sent_count + 1}/{len(media_paths)} archivo(s)"
        if len(current_batch) == 1:
            media_path = current_batch[0]
            with media_path.open("rb") as media_file:
                if media_path.suffix.lower() == ".mp4":
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=media_file,
                        supports_streaming=True,
                    )
                elif media_path.suffix.lower() in {".jpg", ".jpeg", ".png"} and media_path.stat().st_size <= MAX_TELEGRAM_PHOTO_BYTES:
                    await context.bot.send_photo(chat_id=chat_id, photo=media_file)
                else:
                    await context.bot.send_document(chat_id=chat_id, document=media_file)
        else:
            with ExitStack() as stack:
                telegram_media = []
                for media_path in current_batch:
                    media_file = stack.enter_context(media_path.open("rb"))
                    if media_path.suffix.lower() == ".mp4":
                        telegram_media.append(InputMediaVideo(media=media_file, supports_streaming=True))
                    else:
                        telegram_media.append(InputMediaPhoto(media=media_file))
                await context.bot.send_media_group(chat_id=chat_id, media=telegram_media)
        sent_count += len(current_batch)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = f" {user.first_name}" if user and user.first_name else ""
    await update.effective_message.reply_text(
        f"¡Hola{name}! Soy tu bot para descargar fotos y videos de enlaces públicos.\n"
        "Envíame un enlace para empezar o usa /inf para ver todas mis funciones."
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Puedo descargar y enviarte:\n"
        "• Instagram: fotos, videos de publicaciones, carruseles mixtos, Reels e IGTV.\n"
        "• Snapchat Spotlight con yt-dlp y Cobalt como alternativa; TikTok, YouTube, Vimeo, Facebook, X/Twitter, Reddit, Dailymotion, SoundCloud, "
        "Pinterest y Threads, según disponibilidad de cada sitio.\n\n"
        "Pega un enlace HTTPS público. Verás el progreso mientras descargo y envío los archivos. "
        "Los carruseles de Instagram se entregan como álbumes.\n\n"
        "Historias y destacados requieren una sesión de Instagram configurada en el servidor "
        "y solo funcionan si esa cuenta tiene acceso. Para un destacado: "
        "/highlight usuario URL_DEL_DESTACADO.\n\n"
        "No accedo a cuentas privadas ni contenido que requiera iniciar sesión. El límite es "
        f"{MAX_VIDEO_MB} MB por archivo y {MAX_MEDIA_ITEMS} elementos por publicación/carrusel. "
        "Descarga o comparte únicamente contenido que tengas derecho a usar."
    )


async def set_bot_commands(application: Application) -> None:
    commands = [
        BotCommand("start", "Iniciar el bot"),
        BotCommand("inf", "Funciones y descripción"),
        BotCommand("highlight", "Descargar un destacado"),
    ]
    await application.bot.set_my_commands(commands)


async def animate_status(message, state: dict[str, object]) -> None:
    frames = itertools.cycle(("|", "/", "-", "\\"))
    previous_text = ""
    while not state.get("done"):
        frame = next(frames)
        if state.get("phase") == "upload":
            text = f"{frame} Enviando {state.get('upload_status', 'archivo')} a Telegram..."
        else:
            percent = state.get("percent")
            if isinstance(percent, (int, float)):
                text = f"{frame} Descargando {progress_bar(percent)}"
            else:
                downloaded_mb = int(state.get("downloaded") or 0) / (1024 * 1024)
                text = f"{frame} Descargando... {downloaded_mb:.1f} MB"
        if text != previous_text:
            try:
                await message.edit_text(text)
            except TelegramError:
                logger.debug("No se pudo actualizar el mensaje de estado", exc_info=True)
            previous_text = text
        await asyncio.sleep(1)


async def process_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    highlight_owner: str | None = None,
) -> None:
    message = update.effective_message
    try:
        url = validate_url(url)
    except UnsupportedURL as error:
        await message.reply_text(str(error))
        return

    status = await message.reply_text("| Preparando descarga...")
    state: dict[str, object] = {"phase": "download", "percent": None, "downloaded": 0, "done": False}
    animation = asyncio.create_task(animate_status(status, state))
    try:
        with tempfile.TemporaryDirectory(prefix="telegram-media-") as directory:
            instagram_url = classify_instagram_url(url)
            hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
            is_snapchat = hostname == "snapchat.com" or hostname.endswith(".snapchat.com")
            if is_snapchat:
                try:
                    media_path = await asyncio.to_thread(download_video, url, directory, state)
                except VideoTooLarge:
                    raise
                except Exception:
                    logger.warning("yt-dlp no pudo descargar Snapchat; pruebo con Cobalt", exc_info=True)
                    media_path = await asyncio.to_thread(download_snapchat, url, directory, state)
                media_paths = [media_path]
            elif instagram_url:
                try:
                    media_paths = await asyncio.to_thread(
                        download_instagram,
                        url,
                        directory,
                        state,
                        highlight_owner,
                    )
                except (VideoTooLarge, TooManyMedia, InstagramSessionRequired):
                    raise
                except Exception:
                    if instagram_url[0] != "post":
                        raise
                    fallback_directory = Path(directory) / "video-fallback"
                    fallback_directory.mkdir()
                    media_path = await asyncio.to_thread(
                        download_video,
                        url,
                        str(fallback_directory),
                        state,
                    )
                    media_paths = [media_path]
            else:
                media_path = await asyncio.to_thread(download_video, url, directory, state)
                media_paths = [media_path]
            state["phase"] = "upload"
            await send_media_files(context, message.chat_id, media_paths, state)
        state["done"] = True
        await status.edit_text(f"Listo: {len(media_paths)} archivo(s) enviado(s).")
    except (VideoTooLarge, TooManyMedia, InstagramSessionRequired) as error:
        state["done"] = True
        await status.edit_text(str(error))
    except CobaltError as error:
        state["done"] = True
        await status.edit_text(str(error))
    except Exception as error:
        logger.exception("Falló la descarga o el envío")
        state["done"] = True
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        if hostname == "tiktok.com" or hostname.endswith(".tiktok.com"):
            await status.edit_text(
                "TikTok rechazó o cambió la respuesta. Reinicia el bot para aplicar la versión "
                "actualizada de yt-dlp y vuelve a probar más tarde. Si sigue fallando, puede ser "
                "una restricción temporal de TikTok."
            )
        elif (hostname == "instagram.com" or hostname.endswith(".instagram.com")) and (
            "This content isn't available to everyone" in str(error)
        ):
            await status.edit_text(
                "Instagram indica que este Reel no está disponible para todas las audiencias. "
                "Si tu cuenta tiene acceso, configura una sesión válida de Instagram para el bot. "
                "No puedo descargar contenido al que esa cuenta no tenga acceso."
            )
        else:
            await status.edit_text(
                "No pude obtener ese video. Comprueba que el enlace sea público, válido y compatible."
            )
    finally:
        state["done"] = True
        await animation


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        url = extract_url(update.effective_message.text or "")
    except UnsupportedURL as error:
        await update.effective_message.reply_text(str(error))
        return
    await process_link(update, context, url)


async def handle_highlight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /highlight usuario https://www.instagram.com/stories/highlights/ID/"
        )
        return
    username, url = context.args[0].lstrip("@"), context.args[1]
    await process_link(update, context, url, highlight_owner=username)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Configura TELEGRAM_BOT_TOKEN en el entorno o en .env")

    bot = ReconnectingBot(token=token)
    application = Application.builder().bot(bot).post_init(set_bot_commands).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("inf", info))
    application.add_handler(CommandHandler("highlight", handle_highlight))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    logger.info("Bot iniciado")
    application.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()