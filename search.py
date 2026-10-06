TOKEN = "8788753175:AAGFvNuKb36S_qbNON9vuApCjQgKVUkWz50"



import asyncio
import html
import logging
import os
import re
from time import perf_counter

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()


if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")


# Максимальное количество поисков, выполняющихся одновременно.
#
# ВАЖНО:
# Semaphore ограничивает ВЕСЬ ПОИСК целиком:
#
#   search_concurrent()
#       ├── Wikipedia
#       ├── GitHub
#       └── Stack Overflow
#
# То есть при значении 3 одновременно работают максимум
# 3 таких группы.
#
# Это НЕ:
# - лимит только Wikipedia;
# - лимит только GitHub;
# - лимит HTTP-запросов.
#
MAX_CONCURRENT_SEARCHES = 3

# Timeout одного полного поиска
PROVIDER_TIMEOUT = 10

# Максимальное количество задач для /loadtest
MAX_LOADTEST_TASKS = 30

# HTTP timeout
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=PROVIDER_TIMEOUT)


# Semaphore на уровне полного поиска
search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)


HEADERS = {
    "User-Agent": (
        "TelegramSearchBot/1.0 "
        "(https://github.com/Konstantin08081991/search_telegram_bot)"
    ),
    "Accept": "application/json",
}


# ============================================================
# BOT
# ============================================================

dp = Dispatcher()


# ============================================================
# HELPERS
# ============================================================

def clean_html(text: str) -> str:
    """Удаляет HTML и приводит текст к нормальному виду."""

    text = re.sub(
        r"<script.*?</script>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(
        r"<style.*?</style>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# WIKIPEDIA
# ============================================================

async def search_wikipedia(query: str):
    """
    Поиск Wikipedia.

    Сначала ищем в русской Wikipedia,
    затем в английской.

    Весь provider ограничен PROVIDER_TIMEOUT секунд.
    """

    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):

            async with aiohttp.ClientSession(
                headers=HEADERS,
                timeout=HTTP_TIMEOUT,
            ) as session:

                for language in ("ru", "en"):

                    search_url = (
                        f"https://{language}.wikipedia.org"
                        "/w/rest.php/v1/search/page"
                    )

                    params = {
                        "q": query,
                        "limit": 1,
                    }

                    async with session.get(
                        search_url,
                        params=params,
                    ) as response:

                        if response.status == 429:
                            logging.warning(
                                "Wikipedia rate limit: %s",
                                language,
                            )
                            continue

                        response.raise_for_status()

                        data = await response.json()

                    pages = data.get("pages", [])

                    if not pages:
                        continue

                    title = pages[0].get("title")

                    if not title:
                        continue

                    page_url = (
                        f"https://{language}.wikipedia.org"
                        "/w/rest.php/v1/page/"
                        f"{title}/with_html"
                    )

                    async with session.get(page_url) as response:

                        if response.status == 429:
                            logging.warning(
                                "Wikipedia rate limit while reading page: %s",
                                language,
                            )
                            continue

                        response.raise_for_status()

                        page_data = await response.json()

                    html_content = page_data.get("html", "")

                    text = clean_html(html_content)

                    if not text:
                        continue

                    return {
                        "status": "ok",
                        "data": {
                            "title": title,
                            "url": (
                                f"https://{language}.wikipedia.org"
                                f"/wiki/{title.replace(' ', '_')}"
                            ),
                            "text": text[:1500],
                            "language": language,
                        },
                    }

        return {
            "status": "empty",
            "data": None,
        }

    except asyncio.TimeoutError:

        logging.warning("Wikipedia timeout")

        return {
            "status": "timeout",
            "data": None,
        }

    except aiohttp.ClientError as e:

        logging.error(
            "Wikipedia HTTP error: %s",
            e,
        )

        return {
            "status": "error",
            "data": None,
        }

    except ValueError as e:

        logging.error(
            "Wikipedia JSON error: %s",
            e,
        )

        return {
            "status": "error",
            "data": None,
        }

    except Exception as e:

        logging.exception(
            "Wikipedia unexpected error: %s",
            e,
        )

        return {
            "status": "error",
            "data": None,
        }


# ============================================================
# GITHUB
# ============================================================

async def search_github(query: str):
    """Поиск GitHub repositories."""

    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):

            headers = {
                **HEADERS,
                "Accept": "application/vnd.github+json",
            }



            url = "https://api.github.com/search/repositories"

            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": 3,
            }

            async with aiohttp.ClientSession(
                headers=headers,
                timeout=HTTP_TIMEOUT,
            ) as session:

                async with session.get(
                    url,
                    params=params,
                ) as response:

                    response.raise_for_status()

                    data = await response.json()

            items = data.get("items", [])

            results = []

            for item in items:

                results.append(
                    {
                        "name": item.get("full_name"),
                        "description": item.get("description"),
                        "stars": item.get("stargazers_count", 0),
                        "url": item.get("html_url"),
                        "language": item.get("language"),
                    }
                )

            if not results:

                return {
                    "status": "empty",
                    "data": [],
                }

            return {
                "status": "ok",
                "data": results,
            }

    except asyncio.TimeoutError:

        logging.warning("GitHub timeout")

        return {
            "status": "timeout",
            "data": [],
        }

    except aiohttp.ClientError as e:

        logging.error(
            "GitHub HTTP error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }

    except ValueError as e:

        logging.error(
            "GitHub JSON error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }

    except Exception as e:

        logging.exception(
            "GitHub unexpected error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }


# ============================================================
# STACK OVERFLOW
# ============================================================

async def search_stackoverflow(query: str):
    """Поиск Stack Overflow."""

    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):

            url = "https://api.stackexchange.com/2.3/search/advanced"

            params = {
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": "stackoverflow",
                "pagesize": 3,
                "filter": "default",
            }

            async with aiohttp.ClientSession(
                headers=HEADERS,
                timeout=HTTP_TIMEOUT,
            ) as session:

                async with session.get(
                    url,
                    params=params,
                ) as response:

                    response.raise_for_status()

                    data = await response.json()

            items = data.get("items", [])

            results = []

            for item in items:

                results.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("link"),
                        "is_answered": item.get(
                            "is_answered",
                            False,
                        ),
                        "score": item.get(
                            "score",
                            0,
                        ),
                        "answers": item.get(
                            "answer_count",
                            0,
                        ),
                    }
                )

            if not results:

                return {
                    "status": "empty",
                    "data": [],
                }

            return {
                "status": "ok",
                "data": results,
            }

    except asyncio.TimeoutError:

        logging.warning("Stack Overflow timeout")

        return {
            "status": "timeout",
            "data": [],
        }

    except aiohttp.ClientError as e:

        logging.error(
            "Stack Overflow HTTP error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }

    except ValueError as e:

        logging.error(
            "Stack Overflow JSON error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }

    except Exception as e:

        logging.exception(
            "Stack Overflow unexpected error: %s",
            e,
        )

        return {
            "status": "error",
            "data": [],
        }


# ============================================================
# CONCURRENT SEARCH
# ============================================================

async def search_concurrent(query: str):
    """
    Один полный поиск.

    ВАЖНО:
    Semaphore находится СНАРУЖИ provider-запросов.

    Поэтому один permit означает:
        Wikipedia + GitHub + Stack Overflow

    одновременно.

    Например:

        MAX_CONCURRENT_SEARCHES = 3

    Тогда:

        Search #1 -> Wiki + GitHub + SO
        Search #2 -> Wiki + GitHub + SO
        Search #3 -> Wiki + GitHub + SO

    Search #4 ждёт освобождения Semaphore.
    """

    async with search_semaphore:

        logging.info(
            "Search started: %s | active limit=%s",
            query,
            MAX_CONCURRENT_SEARCHES,
        )

        start_time = perf_counter()

        results = await asyncio.gather(
            search_wikipedia(query),
            search_github(query),
            search_stackoverflow(query),
            return_exceptions=True,
        )

        elapsed = perf_counter() - start_time

        wikipedia_result = results[0]

        if isinstance(
            wikipedia_result,
            Exception,
        ):

            logging.error(
                "Wikipedia crashed: %s",
                wikipedia_result,
            )

            wikipedia_result = {
                "status": "error",
                "data": None,
            }

        github_result = results[1]

        if isinstance(
            github_result,
            Exception,
        ):

            logging.error(
                "GitHub crashed: %s",
                github_result,
            )

            github_result = {
                "status": "error",
                "data": [],
            }

        stackoverflow_result = results[2]

        if isinstance(
            stackoverflow_result,
            Exception,
        ):

            logging.error(
                "Stack Overflow crashed: %s",
                stackoverflow_result,
            )

            stackoverflow_result = {
                "status": "error",
                "data": [],
            }

        logging.info(
            "Search finished: %s | %.3f sec",
            query,
            elapsed,
        )

        return (
            wikipedia_result,
            github_result,
            stackoverflow_result,
            elapsed,
        )


# ============================================================
# SEQUENTIAL SEARCH
# ============================================================

async def search_sequential(query: str):
    """Последовательный поиск для benchmark."""

    start_time = perf_counter()

    wikipedia_result = await search_wikipedia(query)

    github_result = await search_github(query)

    stackoverflow_result = await search_stackoverflow(query)

    elapsed = perf_counter() - start_time

    return (
        wikipedia_result,
        github_result,
        stackoverflow_result,
        elapsed,
    )


# ============================================================
# FORMAT RESULTS
# ============================================================

def format_results(
    query: str,
    wikipedia_result,
    github_result,
    stackoverflow_result,
    elapsed: float,
):
    """Форматирует результаты для Telegram."""

    parts = []

    parts.append(
        f"🔎 <b>Результаты поиска:</b> "
        f"<code>{html.escape(query)}</code>\n"
    )

    # --------------------------------------------------------
    # Wikipedia
    # --------------------------------------------------------

    parts.append("📚 <b>Wikipedia</b>")

    wiki_status = wikipedia_result["status"]
    wiki_data = wikipedia_result["data"]

    if wiki_status == "ok" and wiki_data:

        parts.append(
            f"📖 <b>{html.escape(wiki_data['title'])}</b>"
        )

        parts.append(
            html.escape(wiki_data["text"])
        )

        parts.append(
            f'🔗 <a href="{wiki_data["url"]}">Открыть статью</a>'
        )

    elif wiki_status == "empty":

        parts.append(
            "ℹ️ Ничего не найдено."
        )

    elif wiki_status == "timeout":

        parts.append(
            f"⏱️ Wikipedia не ответила "
            f"за {PROVIDER_TIMEOUT} сек."
        )

    else:

        parts.append(
            "⚠️ Wikipedia временно недоступна."
        )

    parts.append("")

    # --------------------------------------------------------
    # GitHub
    # --------------------------------------------------------

    parts.append("🐙 <b>GitHub</b>")

    github_status = github_result["status"]
    github_data = github_result["data"]

    if github_status == "ok" and github_data:

        for repo in github_data:

            name = html.escape(
                repo["name"] or "Unknown"
            )

            description = html.escape(
                repo["description"] or "Без описания"
            )

            language = html.escape(
                repo["language"] or "Unknown"
            )

            parts.append(
                f"📦 <b>{name}</b>\n"
                f"⭐ {repo['stars']}\n"
                f"💻 {language}\n"
                f"{description}\n"
                f'🔗 <a href="{repo["url"]}">GitHub</a>'
            )

    elif github_status == "empty":

        parts.append(
            "ℹ️ Репозитории не найдены."
        )

    elif github_status == "timeout":

        parts.append(
            f"⏱️ GitHub не ответил "
            f"за {PROVIDER_TIMEOUT} сек."
        )

    else:

        parts.append(
            "⚠️ GitHub временно недоступен."
        )

    parts.append("")

    # --------------------------------------------------------
    # Stack Overflow
    # --------------------------------------------------------

    parts.append("💬 <b>Stack Overflow</b>")

    stack_status = stackoverflow_result["status"]
    stack_data = stackoverflow_result["data"]

    if stack_status == "ok" and stack_data:

        for question in stack_data:

            title = html.escape(
                question["title"] or "Без названия"
            )

            answered = (
                "✅ Отвечено"
                if question["is_answered"]
                else "❓ Нет принятого ответа"
            )

            parts.append(
                f"❓ <b>{title}</b>\n"
                f"⭐ Score: {question['score']}\n"
                f"💬 Ответов: {question['answers']}\n"
                f"{answered}\n"
                f'🔗 <a href="{question["url"]}">Stack Overflow</a>'
            )

    elif stack_status == "empty":

        parts.append(
            "ℹ️ Вопросы не найдены."
        )

    elif stack_status == "timeout":

        parts.append(
            f"⏱️ Stack Overflow не ответил "
            f"за {PROVIDER_TIMEOUT} сек."
        )

    else:

        parts.append(
            "⚠️ Stack Overflow временно недоступен."
        )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    results = [
        wikipedia_result,
        github_result,
        stackoverflow_result,
    ]

    working = sum(
        result["status"] == "ok"
        for result in results
    )

    empty = sum(
        result["status"] == "empty"
        for result in results
    )

    timeout = sum(
        result["status"] == "timeout"
        for result in results
    )

    errors = sum(
        result["status"] == "error"
        for result in results
    )

    parts.append("")

    parts.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    parts.append(
        f"📡 Источники: {working}/3 работают"
    )

    parts.append(
        f"ℹ️ Без результатов: {empty}"
    )

    parts.append(
        f"⏱️ Timeout: {timeout}"
    )

    parts.append(
        f"⚠️ Ошибки: {errors}"
    )

    parts.append(
        f"⚡ Время: {elapsed:.3f} сек."
    )

    return "\n".join(parts)


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    text = (
        "👋 <b>Telegram Search Bot</b>\n\n"
        "Бот одновременно ищет информацию в:\n"
        "📚 Wikipedia\n"
        "🐙 GitHub\n"
        "💬 Stack Overflow\n\n"
        "⚡ Источники внутри одного поиска "
        "запускаются одновременно.\n\n"
        f"🛡️ Одновременно разрешено максимум "
        f"<b>{MAX_CONCURRENT_SEARCHES}</b> полных поисков.\n\n"
        f"⏱️ Timeout каждого источника: "
        f"<b>{PROVIDER_TIMEOUT} сек.</b>\n\n"
        "Пример:\n"
        "<code>Python asyncio</code>\n\n"
        "Benchmark:\n"
        "<code>/benchmark Python asyncio</code>\n\n"
        "Нагрузочный тест:\n"
        "<code>/loadtest 10</code>"
    )

    await message.answer(text)


# ============================================================
# BENCHMARK
# ============================================================

@dp.message(Command("benchmark"))
async def benchmark_handler(message: Message):

    query = message.text.replace(
        "/benchmark",
        "",
        1,
    ).strip()

    if not query:

        await message.answer(
            "Использование:\n"
            "<code>/benchmark Python asyncio</code>"
        )

        return

    status_message = await message.answer(
        "📊 Запускаю benchmark..."
    )

    try:

        (
            _,
            _,
            _,
            sequential_time,
        ) = await search_sequential(query)

        (
            _,
            _,
            _,
            concurrent_time,
        ) = await search_concurrent(query)

        speedup = (
            sequential_time / concurrent_time
            if concurrent_time > 0
            else 0
        )

        saved_time = (
            sequential_time - concurrent_time
        )

        improvement = (
            saved_time / sequential_time * 100
            if sequential_time > 0
            else 0
        )

        text = (
            "📊 <b>Результат benchmark</b>\n\n"
            f"🔎 Запрос: "
            f"<code>{html.escape(query)}</code>\n\n"
            "🐢 <b>Последовательно</b>\n"
            f"⏱ {sequential_time:.3f} сек.\n\n"
            "⚡ <b>Конкурентно</b>\n"
            f"⏱ {concurrent_time:.3f} сек.\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🚀 Ускорение: <b>{speedup:.2f}x</b>\n"
            f"💾 Экономия: "
            f"<b>{saved_time:.3f} сек.</b>\n"
            f"📈 Разница: "
            f"<b>{improvement:.1f}%</b>"
        )

        await status_message.edit_text(
            text,
            disable_web_page_preview=True,
        )

    except Exception as e:

        logging.exception(
            "Benchmark error: %s",
            e,
        )

        await status_message.edit_text(
            "❌ Ошибка benchmark."
        )


# ============================================================
# LOAD TEST
# ============================================================

async def run_load_test(
    queries: list[str],
):
    """
    Создаёт несколько одновременно стартующих поисков.

    Все задачи создаются практически одновременно:

        asyncio.create_task(...)

    Но внутри search_concurrent() они упираются
    в search_semaphore.

    Поэтому Semaphore управляет именно количеством
    одновременно выполняющихся ПОЛНЫХ ПОИСКОВ.
    """

    load_start = perf_counter()

    tasks = [
        asyncio.create_task(
            search_concurrent(query)
        )
        for query in queries
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    load_elapsed = (
        perf_counter() - load_start
    )

    successful = 0
    failed = 0

    search_times = []

    for result in results:

        if isinstance(result, Exception):

            failed += 1

            logging.error(
                "Load test task failed: %s",
                result,
            )

            continue

        successful += 1

        elapsed = result[3]

        search_times.append(elapsed)

    return (
        load_elapsed,
        successful,
        failed,
        search_times,
    )


@dp.message(Command("loadtest"))
async def loadtest_handler(message: Message):

    argument = message.text.replace(
        "/loadtest",
        "",
        1,
    ).strip()

    if not argument:

        await message.answer(
            "Использование:\n"
            "<code>/loadtest 10</code>\n\n"
            f"Максимум задач: {MAX_LOADTEST_TASKS}\n"
            f"Semaphore: {MAX_CONCURRENT_SEARCHES}"
        )

        return

    try:

        count = int(argument)

    except ValueError:

        await message.answer(
            "❌ Количество должно быть целым числом.\n\n"
            "Например:\n"
            "<code>/loadtest 10</code>"
        )

        return

    if count < 1:

        await message.answer(
            "❌ Количество должно быть больше 0."
        )

        return

    if count > MAX_LOADTEST_TASKS:

        await message.answer(
            f"❌ Максимум: {MAX_LOADTEST_TASKS} задач."
        )

        return

    # Используем разные запросы, чтобы создать
    # реальную нагрузку на три API.
    queries = [
        f"Python asyncio example {i}"
        for i in range(1, count + 1)
    ]

    status_message = await message.answer(
        "🔥 <b>Запускаю нагрузочный тест</b>\n\n"
        f"Задач: <b>{count}</b>\n"
        f"Semaphore: <b>{MAX_CONCURRENT_SEARCHES}</b>\n\n"
        "Все поиски будут созданы одновременно.\n"
        "Semaphore будет пропускать только "
        f"{MAX_CONCURRENT_SEARCHES} поиска одновременно."
    )

    try:

        (
            total_time,
            successful,
            failed,
            search_times,
        ) = await run_load_test(queries)

        if search_times:

            average_time = (
                sum(search_times)
                / len(search_times)
            )

            max_time = max(search_times)

            min_time = min(search_times)

        else:

            average_time = 0
            max_time = 0
            min_time = 0

        # Теоретическое количество "волн".
        waves = (
            (count + MAX_CONCURRENT_SEARCHES - 1)
            // MAX_CONCURRENT_SEARCHES
        )

        text = (
            "🔥 <b>Нагрузочный тест завершён</b>\n\n"
            f"📦 Всего поисков: <b>{count}</b>\n"
            f"✅ Успешно: <b>{successful}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>\n\n"
            "🛡️ <b>Ограничение Semaphore</b>\n"
            f"Одновременно: <b>{MAX_CONCURRENT_SEARCHES}</b>\n"
            f"Расчётных волн: <b>{waves}</b>\n\n"
            "⏱ <b>Время</b>\n"
            f"Общее: <b>{total_time:.3f} сек.</b>\n"
            f"Минимум: <b>{min_time:.3f} сек.</b>\n"
            f"Среднее: <b>{average_time:.3f} сек.</b>\n"
            f"Максимум: <b>{max_time:.3f} сек.</b>\n\n"
            "💡 Semaphore ограничивает "
            "<b>полные поиски целиком</b>.\n\n"
            "Внутри каждого разрешённого поиска "
            "Wikipedia + GitHub + Stack Overflow "
            "по-прежнему выполняются одновременно."
        )

        await status_message.edit_text(
            text,
            disable_web_page_preview=True,
        )

    except Exception as e:

        logging.exception(
            "Load test error: %s",
            e,
        )

        await status_message.edit_text(
            "❌ Ошибка нагрузочного теста."
        )


# ============================================================
# NORMAL SEARCH
# ============================================================

@dp.message()
async def search_handler(message: Message):

    if not message.text:
        return

    query = message.text.strip()

    if not query:
        return

    # Команды обрабатываются command handlers.
    if query.startswith("/"):
        return

    status_message = await message.answer(
        "🔎 <b>Ищу...</b>\n\n"
        "📚 Wikipedia\n"
        "🐙 GitHub\n"
        "💬 Stack Overflow\n\n"
        "⚡ Все источники запускаются одновременно.\n"
        f"🛡️ Максимум одновременно: "
        f"{MAX_CONCURRENT_SEARCHES} полных поиска."
    )

    try:

        (
            wikipedia_result,
            github_result,
            stackoverflow_result,
            elapsed,
        ) = await search_concurrent(query)

        result = format_results(
            query,
            wikipedia_result,
            github_result,
            stackoverflow_result,
            elapsed,
        )

        await status_message.edit_text(
            result,
            disable_web_page_preview=True,
        )

    except Exception as e:

        logging.exception(
            "General search error: %s",
            e,
        )

        await status_message.edit_text(
            "❌ Произошла ошибка "
            "при выполнении поиска."
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    try:

        logging.info(
            "Telegram bot started"
        )

        logging.info(
            "Provider timeout: %s seconds",
            PROVIDER_TIMEOUT,
        )

        logging.info(
            "Max concurrent searches: %s",
            MAX_CONCURRENT_SEARCHES,
        )

        await dp.start_polling(bot)

    finally:

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())