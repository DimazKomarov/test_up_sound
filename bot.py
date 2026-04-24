import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from yandex_music import Client

# Резервный regex на случай нестандартного текста с URL.
YA_TRACK_RE = re.compile(
    r"(?:music\.yandex\.[^/]+/album/\d+/track/|music\.yandex\.[^/]+/track/)(\d+)"
)


@dataclass
class TrackInfo:
    title: str
    artists: str
    duration_seconds: int

    @property
    def duration_hhmmss(self) -> str:
        minutes, seconds = divmod(self.duration_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class YandexMusicService:
    def __init__(self, token: str) -> None:
        self.client = Client(token).init()

    def get_track_info(self, track_id: str) -> Optional[TrackInfo]:
        tracks = self.client.tracks([track_id])
        if not tracks:
            return None

        track = tracks[0]
        if not track:
            return None

        artist_names = (
            ", ".join(track.artists_name()) if track.artists_name() else "Unknown"
        )
        duration_ms = track.duration_ms or 0

        return TrackInfo(
            title=track.title or "Без названия",
            artists=artist_names,
            duration_seconds=duration_ms // 1000,
        )


def extract_track_id(text: str) -> Optional[str]:
    candidate = text.strip()

    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        parsed = None

    if parsed and parsed.netloc and "music.yandex." in parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        # Поддержка:
        # /album/<album_id>/track/<track_id>
        # /track/<track_id>
        if (
            len(parts) >= 4
            and parts[0] == "album"
            and parts[2] == "track"
            and parts[3].isdigit()
        ):
            return parts[3]
        if len(parts) >= 2 and parts[0] == "track" and parts[1].isdigit():
            return parts[1]

    # Fallback для текста, где URL окружен лишними символами.
    match = YA_TRACK_RE.search(text)
    return match.group(1) if match else None


def setup_handlers(dp: Dispatcher, ym_service: YandexMusicService) -> None:
    @dp.message(CommandStart())
    async def start_handler(message: Message) -> None:
        await message.answer(
            "Привет! Отправь ссылку на трек из Яндекс.Музыки, и я верну информацию о нём."
        )

    @dp.message(F.text)
    async def text_handler(message: Message) -> None:
        if not message.text:
            return

        track_id = extract_track_id(message.text)
        if not track_id:
            await message.answer(
                "Не вижу корректной ссылки на трек. Пример: https://music.yandex.ru/album/12345/track/67890"
            )
            return

        try:
            track = ym_service.get_track_info(track_id)
        except Exception:
            logging.exception("Ошибка при запросе к yandex-music")
            await message.answer(
                "Не удалось получить данные о треке. Проверьте токен и ссылку."
            )
            return

        if not track:
            await message.answer("Трек не найден.")
            return

        await message.answer(
            f"Название: {track.title}\n"
            f"Артист: {track.artists}\n"
            f"Длительность: {track.duration_hhmmss}"
        )


async def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )

    # override=True, чтобы значения из .env обновлялись даже если переменные
    # уже были выставлены в системном окружении ранее.
    load_dotenv(override=True)

    bot_token = os.getenv("BOT_TOKEN")
    ym_token = os.getenv("YANDEX_MUSIC_TOKEN")

    if not bot_token or not ym_token:
        raise RuntimeError(
            "Нужно задать BOT_TOKEN и YANDEX_MUSIC_TOKEN. "
            "Создайте файл .env на основе .env.example или задайте переменные окружения вручную."
        )

    bot = Bot(token=bot_token)
    dp = Dispatcher()
    ym_service = YandexMusicService(ym_token)

    setup_handlers(dp, ym_service)

    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError as exc:
        raise RuntimeError(
            "Неверный BOT_TOKEN: Telegram вернул Unauthorized. "
            "Проверьте токен в .env (получите новый через @BotFather при необходимости)."
        ) from exc


if __name__ == "__main__":
    asyncio.run(main())
