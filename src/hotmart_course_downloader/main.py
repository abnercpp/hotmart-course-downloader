from asyncio import run, gather, Semaphore, Event
from dataclasses import dataclass
from multiprocessing import cpu_count
from pathlib import Path
from tomllib import loads as toml_loads
from typing import Callable, Awaitable

import yt_dlp.utils.networking
from aiofiles import open as aio_open
from playwright.async_api import async_playwright, Page, Route, Locator
from slugify import slugify
from yt_dlp import YoutubeDL


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    consumer_portal_url: str
    courses_portal_url: str
    origin_url: str
    referer_url: str

    login_portal_url_pattern: str
    m3u8_master_url_pattern: str

    accept_cookies_btn_selector: str

    course_title_selector: str
    purchased_course_card_selector: str
    course_module_card_selector: str
    course_module_index_selector: str
    course_module_title_selector: str
    course_lesson_card_selector: str
    course_lesson_title_selector: str
    course_complete_lesson_selector: str
    course_main_content_selector: str
    course_video_content_selector: str
    video_part_selector: str
    video_part_duration_selector: str
    active_video_part_selector: str

    sso_username_txt_selector: str
    sso_password_txt_selector: str
    sso_login_btn_selector: str

    sso_user_email: str
    sso_user_password: str

    screenshot_extension: str
    video_format: str

    downloads_folder: str


async def main(config: Config) -> None:
    pw_instance = await async_playwright().start()

    try:
        async with (await pw_instance.firefox.launch(headless=False) as browser,
                    await browser.new_context() as ctx,
                    await ctx.new_page() as page):
            await page.goto(config.consumer_portal_url)
            await page.wait_for_url(config.login_portal_url_pattern)
            await page.click(config.accept_cookies_btn_selector)
            await page.type(config.sso_username_txt_selector, config.sso_user_email)
            await page.type(config.sso_password_txt_selector, config.sso_user_password)
            await page.click(config.sso_login_btn_selector)
            await page.wait_for_url(config.courses_portal_url)
            course_cards_locator = page.locator(config.purchased_course_card_selector)
            await course_cards_locator.last.wait_for()
            course_cards = await course_cards_locator.all()
            semaphore = Semaphore(len(course_cards))
            ctx.on('page', lambda cur_page: _on_course_entered(cur_page, semaphore, config))
            await gather(*[_click_course_card(course_card, semaphore) for course_card in course_cards])
            await semaphore.acquire()
    finally:
        await pw_instance.stop()


async def _click_course_card(course_card: Locator, semaphore: Semaphore) -> None:
    assert await semaphore.acquire()
    await course_card.click()


async def _on_course_entered(page: Page, semaphore: Semaphore, config: Config) -> None:
    try:
        module_cards_selector = page.locator(config.course_module_card_selector)
        await module_cards_selector.last.wait_for()

        for module_no, module_card in enumerate(await module_cards_selector.all()):
            await module_card.click()
            lesson_cards_selector = module_card.locator(config.course_lesson_card_selector)
            await lesson_cards_selector.last.wait_for()

            for lesson_no, lesson_card in enumerate(await lesson_cards_selector.all()):
                await _save_lesson_contents(module_card,
                                            lesson_card,
                                            module_no,
                                            lesson_no,
                                            lambda: lesson_card.click(),
                                            config)

                video_parts_locator = page.locator(config.video_part_selector)
                if not await video_parts_locator.last.is_visible():
                    continue

                video_parts = await video_parts_locator.all()
                video_parts.pop(0)

                for video_part in video_parts:
                    await _save_lesson_contents(module_card,
                                                lesson_card,
                                                module_no,
                                                lesson_no,
                                                lambda: video_part.click(),
                                                config)
    finally:
        semaphore.release()


async def _save_lesson_contents(module_card: Locator,
                                lesson_card: Locator,
                                module_no: int,
                                lesson_no: int,
                                click_to_update: Callable[[], Awaitable],
                                config: Config) -> None:
    event = Event()
    await lesson_card.page.route(config.m3u8_master_url_pattern,
                                 lambda route: _on_m3u8_master_request(module_card,
                                                                       lesson_card,
                                                                       module_no,
                                                                       lesson_no,
                                                                       route,
                                                                       event,
                                                                       config))
    await click_to_update()
    main_content_selector = lesson_card.page.locator(config.course_main_content_selector)
    await main_content_selector.wait_for()
    if await lesson_card.page.is_visible(config.course_video_content_selector):
        await event.wait()
    else:
        path = await _ensure_path_created(module_card, lesson_card, module_no, lesson_no, config)
        await main_content_selector.screenshot(path=f'{path.resolve()}.{config.screenshot_extension}')

    await lesson_card.page.unroute(config.m3u8_master_url_pattern)
    event.clear()


async def _ensure_path_created(module_card: Locator,
                               lesson_card: Locator,
                               module_no: int,
                               lesson_no: int,
                               config: Config) -> Path:
    course_title = slugify(await module_card.page.text_content(config.course_title_selector))

    mod_index = slugify(
        await module_card.locator(config.course_module_index_selector).text_content())

    mod_title = slugify(
        await module_card.locator(config.course_module_title_selector).text_content())

    les_title = slugify(
        await lesson_card.locator(config.course_lesson_title_selector).text_content())

    path = Path(f'{config.downloads_folder}/{course_title}/[{module_no:02d}] [{mod_index}] {mod_title}')
    path.mkdir(parents=True, exist_ok=True)

    return path.joinpath(f'[{lesson_no:02d}] {les_title}')


async def _on_m3u8_master_request(module_card: Locator,
                                  lesson_card: Locator,
                                  module_no: int,
                                  lesson_no: int,
                                  route: Route,
                                  event: Event,
                                  config: Config) -> None:
    try:
        active_playlist_video = lesson_card.page.locator(config.active_video_part_selector)
        unresolved_base_path = await _ensure_path_created(module_card, lesson_card, module_no, lesson_no, config)
        resolved_base_path = str(unresolved_base_path.resolve())

        if await active_playlist_video.is_visible():
            active_playlist_title = slugify(await active_playlist_video.text_content())
            active_playlist_duration = slugify(
                await lesson_card.page.text_content(config.video_part_duration_selector))
            active_playlist_title = active_playlist_title.removesuffix(active_playlist_duration)

            resolved_base_path += f' ({active_playlist_title})'

        yt_dlp.utils.networking.std_headers['Referer'] = config.referer_url
        yt_dlp.utils.networking.std_headers['Origin'] = config.origin_url

        ydl_opts = {
            'format': config.video_format,
            'outtmpl': f'{resolved_base_path}.%(ext)s',
            'headers': route.request.headers,
            'concurrent_fragment_downloads': cpu_count(),
            'hls_segment_threads': cpu_count()
        }

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([route.request.url])

        complete_lesson_btn_locator = lesson_card.page.locator(config.course_complete_lesson_selector)
        if await complete_lesson_btn_locator.is_visible():
            await complete_lesson_btn_locator.click()

        await route.fulfill()
    finally:
        event.set()


async def _main() -> None:
    config = await _init_config()
    await main(config)


async def _init_config() -> Config:
    async with (aio_open('../../config/settings.toml', 'r') as settings_toml,
                aio_open('../../config/credentials.toml') as credentials_toml):
        (settings_contents, credentials_contents) = await gather(settings_toml.read(), credentials_toml.read())
        settings = toml_loads(settings_contents)
        credentials = toml_loads(credentials_contents)
        return Config(consumer_portal_url=settings['hotmart']['urls']['consumer-portal'],
                      courses_portal_url=settings['hotmart']['urls']['courses-portal'],
                      origin_url=settings['hotmart']['urls']['origin'],
                      referer_url=settings['hotmart']['urls']['referer'],
                      login_portal_url_pattern=settings['hotmart']['urls']['patterns']['login-portal'],
                      m3u8_master_url_pattern=settings['hotmart']['urls']['patterns']['m3u8-master'],
                      accept_cookies_btn_selector=settings['hotmart']['selectors']['accept-cookies-btn'],
                      course_title_selector=settings['hotmart']['selectors']['courses']['course-title'],
                      purchased_course_card_selector=settings['hotmart']['selectors']['courses']['purchased-card'],
                      course_module_card_selector=settings['hotmart']['selectors']['courses']['module-card'],
                      course_module_index_selector=settings['hotmart']['selectors']['courses']['module-index'],
                      course_module_title_selector=settings['hotmart']['selectors']['courses']['module-title'],
                      course_lesson_card_selector=settings['hotmart']['selectors']['courses']['lesson-card'],
                      course_lesson_title_selector=settings['hotmart']['selectors']['courses']['lesson-title'],
                      course_complete_lesson_selector=settings['hotmart']['selectors']['courses']['clear-lesson-btn'],
                      course_main_content_selector=settings['hotmart']['selectors']['courses']['main-content-section'],
                      course_video_content_selector=settings['hotmart']['selectors']['courses']['video-section'],
                      video_part_selector=settings['hotmart']['selectors']['courses']['video-part'],
                      video_part_duration_selector=settings['hotmart']['selectors']['courses']['video-part-duration'],
                      active_video_part_selector=settings['hotmart']['selectors']['courses']['video-part-active'],
                      sso_username_txt_selector=settings['hotmart']['selectors']['sso']['username-txt'],
                      sso_password_txt_selector=settings['hotmart']['selectors']['sso']['password-txt'],
                      sso_login_btn_selector=settings['hotmart']['selectors']['sso']['login-btn'],
                      sso_user_email=credentials['hotmart']['auth']['sso']['email'],
                      sso_user_password=credentials['hotmart']['auth']['sso']['password'],
                      screenshot_extension=settings['screenshot']['ext'],
                      video_format=settings['video']['format'],
                      downloads_folder=settings['downloads']['folder'])


if __name__ == '__main__':
    run(_main())
