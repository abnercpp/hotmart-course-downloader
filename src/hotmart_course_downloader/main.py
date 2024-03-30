from asyncio import run
from configparser import ConfigParser
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    config = init_config()
    pw_instance = await async_playwright().start()

    try:
        async with (await pw_instance.firefox.launch(headless=False) as browser,
                    await browser.new_context() as ctx,
                    await ctx.new_page() as page):
            await page.goto(config['hotmart.urls']['consumer-portal'])
            await page.wait_for_url(config['hotmart.urls.patterns']['login-portal'])
            await page.click(config['hotmart.selectors']['accept-cookies-btn'])
    finally:
        await pw_instance.stop()


def init_config() -> ConfigParser:
    config_path = Path('../../config/settings.ini')

    if not config_path.is_file():
        raise FileNotFoundError(f'Missing file {config_path.resolve()}')

    parser = ConfigParser()
    parser.read(config_path)
    return parser


if __name__ == '__main__':
    run(main())
