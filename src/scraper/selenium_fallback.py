"""Headless Chrome renderer for JS-rendered pages (bina.az search results)."""
import logging
import os
import time
from contextlib import contextmanager

from src.config import REQUEST_DELAY_SEC, USER_AGENT

log = logging.getLogger(__name__)

CARD_SELECTOR = '[data-cy="item-card"]'


@contextmanager
def chrome_driver():
    # Imported lazily so the app works without selenium when only cache/DB is used.
    from selenium import webdriver

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=az")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_argument("--window-size=1280,1000")  # small headless windows never trigger the loader
    # In Docker we use Debian's chromium + chromedriver; locally Selenium Manager finds Chrome.
    chrome_bin, driver_path = os.environ.get("CHROME_BIN"), os.environ.get("CHROMEDRIVER_PATH")
    service = None
    if chrome_bin and os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin
        if driver_path and os.path.exists(driver_path):
            from selenium.webdriver.chrome.service import Service

            service = Service(executable_path=driver_path)
    driver = webdriver.Chrome(options=opts, service=service) if service else webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    try:
        yield driver
    finally:
        driver.quit()


def render_page(url: str, driver=None, wait_sec: float = 15.0, pages: int = 1) -> str:
    """Load url, wait for listing cards, scroll to load `pages` batches, return HTML.

    bina.az uses infinite scroll (~32 cards per batch); one "page" = one batch.
    Note: the "Daha çox" link on the page belongs to the VIP block, not to pagination.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    def _count(d) -> int:
        return len(d.find_elements(By.CSS_SELECTOR, CARD_SELECTOR))

    def _render(d):
        d.get(url)
        try:
            WebDriverWait(d, wait_sec).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SELECTOR))
            )
            time.sleep(1.0)  # let the remaining cards hydrate
        except Exception:
            log.warning("No listing cards rendered for %s", url)
            return d.page_source
        for _ in range(pages - 1):
            before = _count(d)
            time.sleep(REQUEST_DELAY_SEC)  # politeness between batch loads
            # Infinite scroll: bring the last card into view, then nudge past it to trigger the loader.
            cards = d.find_elements(By.CSS_SELECTOR, CARD_SELECTOR)
            d.execute_script("arguments[0].scrollIntoView({block: 'end'});", cards[-1])
            d.execute_script("window.scrollBy(0, 400);")
            try:
                WebDriverWait(d, wait_sec).until(lambda x: _count(x) > before)
            except Exception:
                break  # no more results
        return d.page_source

    if driver is not None:
        html = _render(driver)
    else:
        with chrome_driver() as d:
            html = _render(d)
    time.sleep(REQUEST_DELAY_SEC)
    return html
