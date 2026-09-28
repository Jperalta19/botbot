#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -t 0 ]]; then
    printf 'Ejecuta init.sh desde una terminal interactiva.\n' >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf 'No encuentro python3. Instala Python 3.10 o posterior y vuelve a ejecutar init.sh.\n' >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf 'Se requiere Python 3.10 o posterior.\n' >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    cp .env.example .env
fi

set_env_value() {
    local key="$1"
    local value="$2"
    local temp_file
    temp_file="$(mktemp .env.XXXXXX)"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 {
            if (!found) {
                print key "=" value
                found = 1
            }
            next
        }
        { print }
        END {
            if (!found) print key "=" value
        }
    ' .env > "$temp_file"
    chmod 600 "$temp_file"
    mv "$temp_file" .env
}

get_env_value() {
    local key="$1"
    awk -v key="$key" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' .env
}

printf 'Preparando el bot de Telegram...\n'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --upgrade --pre yt-dlp

token="$(get_env_value TELEGRAM_BOT_TOKEN)"
if [[ -z "$token" || "$token" == 'pega_aqui_el_token_de_BotFather' ]]; then
    printf 'Crea un bot con @BotFather y pega aquí su token. La entrada no se mostrará.\n'
    IFS= read -r -s -p 'Token de Telegram: ' token
    printf '\n'
    if [[ -z "$token" ]]; then
        printf 'El token no puede quedar vacío.\n' >&2
        exit 1
    fi
    set_env_value TELEGRAM_BOT_TOKEN "$token"
    unset token
else
    printf 'Conservo el token de Telegram ya configurado.\n'
fi

read -r -p '¿Configurar historias y destacados de Instagram? [s/N] ' configure_instagram
if [[ "$configure_instagram" =~ ^[sS]([iI])?$ ]]; then
    instagram_user="$(get_env_value INSTAGRAM_USERNAME)"
    if [[ -z "$instagram_user" ]]; then
        read -r -p 'Usuario de Instagram: ' instagram_user
    else
        read -r -p "Usuario de Instagram [$instagram_user]: " input_user
        instagram_user="${input_user:-$instagram_user}"
    fi
    if [[ -z "$instagram_user" ]]; then
        printf 'Se omite la configuración de Instagram.\n'
    else
        default_session="$HOME/.config/instaloader/session-$instagram_user"
        session_file="$(get_env_value INSTAGRAM_SESSION_FILE)"
        if [[ -n "$session_file" && -f "$session_file" ]]; then
            read -r -p "Usar la sesión configurada en $session_file? [S/n] " reuse_session
        else
            reuse_session='n'
        fi

        if [[ ! "$reuse_session" =~ ^[nN]([oO])?$ ]]; then
            session_file="${session_file:-$default_session}"
        else
            printf 'Instaloader abrirá su inicio de sesión interactivo; escribe tu contraseña solo allí.\n'
            .venv/bin/instaloader --login="$instagram_user"
            session_file="$default_session"
        fi

        if [[ ! -f "$session_file" ]]; then
            read -r -p "Ruta del archivo de sesión de Instaloader [$default_session]: " input_session
            session_file="${input_session:-$default_session}"
        fi
        if [[ -f "$session_file" ]]; then
            set_env_value INSTAGRAM_USERNAME "$instagram_user"
            set_env_value INSTAGRAM_SESSION_FILE "$session_file"
            printf 'Sesión de Instagram configurada.\n'
        else
            printf 'No encontré el archivo de sesión; historias y destacados quedarán desactivados.\n' >&2
        fi
    fi
fi

chmod 600 .env
.venv/bin/python -m py_compile bot.py
printf 'Configuración lista.\n'

if [[ "${1:-}" == '--setup-only' ]]; then
    printf 'Para iniciar el bot: .venv/bin/python bot.py\n'
    exit 0
fi

stopping=0
bot_pid=''

stop_bot() {
    stopping=1
    if [[ -n "$bot_pid" ]]; then
        kill -TERM "$bot_pid" 2>/dev/null || true
    fi
}

trap stop_bot INT TERM
printf 'Iniciando el bot. Pulsa Ctrl+C para detenerlo.\n'
while [[ "$stopping" -eq 0 ]]; do
    .venv/bin/python bot.py &
    bot_pid=$!
    if wait "$bot_pid"; then
        exit_code=0
    else
        exit_code=$?
    fi
    bot_pid=''

    if [[ "$stopping" -eq 1 ]]; then
        break
    fi

    printf 'El bot terminó con código %s. Se reiniciará en 5 segundos.\n' "$exit_code" >&2
    sleep 5 &
    sleep_pid=$!
    if wait "$sleep_pid"; then
        :
    fi
done

printf 'Bot detenido.\n'