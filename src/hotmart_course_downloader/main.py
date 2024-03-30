from asyncio import run, gather, Semaphore, Event
from dataclasses import dataclass
from tomllib import loads as toml_loads

from aiofiles import open as aio_open
from playwright.async_api import async_playwright, Page, Route, Locator


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    consumer_portal_url: str
    courses_portal_url: str

    login_portal_url_pattern: str
    m3u8_master_url_pattern: str

    accept_cookies_btn_selector: str

    purchased_course_card_selector: str
    course_module_card_selector: str
    course_lesson_card_selector: str
    course_main_content_selector: str
    course_video_content_selector: str

    sso_username_txt_selector: str
    sso_password_txt_selector: str
    sso_login_btn_selector: str

    sso_user_email: str
    sso_user_password: str


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

        event = Event()

        await page.route(config.m3u8_master_url_pattern,
                         lambda route: _on_m3u8_master_request(page, route, event, config))

        for module_card in await module_cards_selector.all():
            await module_card.click()
            lesson_cards_selector = page.locator(config.course_lesson_card_selector)
            await lesson_cards_selector.last.wait_for()
            for lesson_card in await lesson_cards_selector.all():
                await lesson_card.click()
                main_content_selector = page.locator(config.course_main_content_selector)
                await main_content_selector.wait_for()
                if await page.is_visible(config.course_video_content_selector):
                    await event.wait()
                event.clear()
    finally:
        semaphore.release()


async def _on_m3u8_master_request(page: Page, route: Route, event: Event, config: Config) -> None:
    print('Downloading!', page, route, event, config)
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
                      login_portal_url_pattern=settings['hotmart']['urls']['patterns']['login-portal'],
                      m3u8_master_url_pattern=settings['hotmart']['urls']['patterns']['m3u8-master'],
                      accept_cookies_btn_selector=settings['hotmart']['selectors']['accept-cookies-btn'],
                      purchased_course_card_selector=settings['hotmart']['selectors']['courses']['purchased-card'],
                      course_module_card_selector=settings['hotmart']['selectors']['courses']['module-card'],
                      course_lesson_card_selector=settings['hotmart']['selectors']['courses']['lesson-card'],
                      course_main_content_selector=settings['hotmart']['selectors']['courses']['main-content-section'],
                      course_video_content_selector=settings['hotmart']['selectors']['courses']['video-section'],
                      sso_username_txt_selector=settings['hotmart']['selectors']['sso']['username_txt'],
                      sso_password_txt_selector=settings['hotmart']['selectors']['sso']['password_txt'],
                      sso_login_btn_selector=settings['hotmart']['selectors']['sso']['login_btn'],
                      sso_user_email=credentials['hotmart']['auth']['sso']['email'],
                      sso_user_password=credentials['hotmart']['auth']['sso']['password'])


if __name__ == '__main__':
    run(_main())
