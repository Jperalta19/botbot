#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_CONFIG="$PROJECT_DIR/.github-repo"

if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then
    printf 'Uso: ./up.sh [usuario/repositorio | URL de GitHub]\n'
    printf 'La primera ejecución solicita el repositorio y lo guarda en .github-repo.\n'
    exit 0
fi

repository="${1:-${GITHUB_REPOSITORY:-}}"
if [[ -z "$repository" && -f "$REPO_CONFIG" ]]; then
    IFS= read -r repository < "$REPO_CONFIG"
fi

if [[ -z "$repository" ]]; then
    if [[ ! -t 0 ]]; then
        printf 'Falta el repositorio. Ejecuta ./up.sh usuario/repositorio.\n' >&2
        exit 1
    fi
    read -r -p 'Repositorio GitHub (usuario/repositorio): ' repository
fi

if [[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    repository_url="https://github.com/$repository.git"
elif [[ "$repository" =~ ^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(\.git)?/?$ ]]; then
    repository="${BASH_REMATCH[1]}"
    repository_url="https://github.com/$repository.git"
else
    printf 'Repositorio no válido. Usa usuario/repositorio o https://github.com/usuario/repositorio.\n' >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    printf 'Se necesita Git para descargar actualizaciones.\n' >&2
    exit 1
fi

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/botbot-update.XXXXXX")"
cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT

printf 'Descargando %s desde GitHub...\n' "$repository"
git clone --depth 1 "$repository_url" "$temporary_dir/source"

for required_file in bot.py requirements.txt init.sh; do
    if [[ ! -f "$temporary_dir/source/$required_file" ]]; then
        printf 'El repositorio no contiene %s; no se aplicaron cambios.\n' "$required_file" >&2
        exit 1
    fi
done

backup_dir="$PROJECT_DIR/.update-backups/$(date +%Y%m%d-%H%M%S)"
updated=0
while IFS= read -r -d '' source_file; do
    relative_path="${source_file#"$temporary_dir/source/"}"
    case "$relative_path" in
        .git/*|.env|.venv|.venv/*|__pycache__|__pycache__/*|.github-repo|.update-backups|.update-backups/*)
            continue
            ;;
    esac

    destination="$PROJECT_DIR/$relative_path"
    if [[ -e "$destination" ]]; then
        backup_path="$backup_dir/$relative_path"
        mkdir -p "$(dirname "$backup_path")"
        cp -a "$destination" "$backup_path"
    fi
    mkdir -p "$(dirname "$destination")"
    cp -a "$source_file" "$destination"
    updated=$((updated + 1))
done < <(find "$temporary_dir/source" -type f ! -path "$temporary_dir/source/.git/*" -print0)

printf '%s\n' "$repository" > "$REPO_CONFIG"
chmod 600 "$REPO_CONFIG"

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    "$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
    "$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade --pre yt-dlp
else
    printf 'No hay entorno virtual; ejecuta ./init.sh para instalar dependencias.\n'
fi

if [[ -d "$backup_dir" ]]; then
    printf 'Respaldos de archivos reemplazados: %s\n' "$backup_dir"
fi
printf 'Actualización lista: %s archivo(s). Reinicia el bot con ./init.sh.\n' "$updated"