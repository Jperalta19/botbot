# Bot de descargas para Telegram

Bot en Python para recibir enlaces y enviar el contenido multimedia al chat. Incluye mensajes de estado animados durante la descarga y la subida a Telegram.

## Instagram

Admite publicaciones de foto o video, carruseles mixtos, Reels e IGTV (ahora publicaciones de video). Los carruseles se envían como álbumes de Telegram.

Las historias y los destacados requieren una sesión válida de Instagram guardada localmente y solo se pueden descargar si la cuenta de esa sesión tiene acceso. Para un destacado, envía `/highlight usuario URL_DEL_DESTACADO`. Para crear una sesión local:

```bash
.venv/bin/instaloader --login=TU_USUARIO
```

Configura `INSTAGRAM_USERNAME` y `INSTAGRAM_SESSION_FILE` en `.env` apuntando al archivo creado. No compartas ese archivo ni lo subas al repositorio.

No se eluden restricciones de acceso. Usa el bot solo con contenido que tengas derecho a descargar y compartir, respetando los términos de la plataforma y los derechos de sus autores.

## Otros sitios

Se habilitan TikTok, YouTube, Vimeo, Facebook, X/Twitter, Reddit, Dailymotion, SoundCloud, Pinterest y Threads mediante `yt-dlp`. La extracción depende de los cambios y límites de cada plataforma y, para estos sitios, se procesa un video por enlace. Solo se aceptan dominios incluidos en `ALLOWED_DOMAINS`.

## Instalación

Requiere Python 3.10 o posterior y un token de bot creado con [@BotFather](https://t.me/BotFather).

```bash
./init.sh
```

El instalador crea el entorno virtual, instala dependencias, actualiza `yt-dlp` al canal nightly recomendado para corregir cambios de extractores, solicita el token sin mostrarlo, conserva las opciones existentes de `.env` y arranca el bot bajo un supervisor. Ante una caída inesperada, lo reinicia a los 5 segundos. Los errores de conexión con Telegram también se reintentan cada 5 segundos. `Ctrl+C` detiene el bot de forma normal.

Para configurar sin iniciarlo, usa `./init.sh --setup-only`; luego inicia con `./init.sh` para conservar el supervisor. Si ejecutas directamente:

```bash
.venv/bin/python bot.py
```

el bot mantiene los reintentos de red, pero no reinicia el proceso si este termina.

El inicio de sesión opcional de Instagram se realiza en el terminal de Instaloader; la contraseña no se guarda en `.env`.

Si un sitio deja de funcionar, actualiza y reinicia el bot con `./init.sh --setup-only` y `.venv/bin/python bot.py`.

Para actualizar el código desde GitHub, ejecuta `./up.sh usuario/repositorio`. La primera vez también puedes ejecutar `./up.sh` y escribir el repositorio cuando lo solicite. El script respalda los archivos que reemplaza, conserva `.env` y `.venv`, e instala las dependencias nuevas. Luego reinicia con `./init.sh`.

El Bot API alojado de Telegram limita el envío de archivos a aproximadamente 50 MB; por eso el bot usa 49 MB por archivo como límite predeterminado. `MAX_MEDIA_ITEMS` limita a 20 elementos cada publicación/carrusel. Para restringir o ampliar los sitios permitidos, define `ALLOWED_DOMAINS` como una lista de dominios separados por comas.

## Uso

Abre el chat con tu bot, envía `/start` y pega un enlace HTTPS. El mismo mensaje de estado muestra la actividad de descarga y de envío. Los archivos temporales se eliminan al terminar.