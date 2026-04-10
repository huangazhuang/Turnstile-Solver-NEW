import os
import sys
import time
import uuid
import random
import logging
import asyncio
from typing import Optional, Union
import argparse
from quart import Quart, request, jsonify
from camoufox.async_api import AsyncCamoufox
from patchright.async_api import async_playwright
from db_results import init_db, save_result, load_result, cleanup_old_results, cleanup_stuck_tasks
from browser_configs import browser_config
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box



COLORS = {
    'MAGENTA': '\033[35m',
    'BLUE': '\033[34m',
    'GREEN': '\033[32m',
    'YELLOW': '\033[33m',
    'RED': '\033[31m',
    'RESET': '\033[0m',
}


class CustomLogger(logging.Logger):
    @staticmethod
    def format_message(level, color, message):
        timestamp = time.strftime('%H:%M:%S')
        return f"[{timestamp}] [{COLORS.get(color)}{level}{COLORS.get('RESET')}] -> {message}"

    def debug(self, message, *args, **kwargs):
        super().debug(self.format_message('DEBUG', 'MAGENTA', message), *args, **kwargs)

    def info(self, message, *args, **kwargs):
        super().info(self.format_message('INFO', 'BLUE', message), *args, **kwargs)

    def success(self, message, *args, **kwargs):
        super().info(self.format_message('SUCCESS', 'GREEN', message), *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        super().warning(self.format_message('WARNING', 'YELLOW', message), *args, **kwargs)

    def error(self, message, *args, **kwargs):
        super().error(self.format_message('ERROR', 'RED', message), *args, **kwargs)


logging.setLoggerClass(CustomLogger)
logger = logging.getLogger("TurnstileAPIServer")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)


class TurnstileAPIServer:

    def __init__(self, headless: bool, useragent: Optional[str], debug: bool, browser_type: str, thread: int, proxy_support: bool, use_random_config: bool = False, browser_name: Optional[str] = None, browser_version: Optional[str] = None):
        self.app = Quart(__name__)
        self.debug = debug
        self.browser_type = browser_type
        self.headless = headless
        self.thread_count = thread
        self.proxy_support = proxy_support
        self.browser_pool = asyncio.Queue()
        self.use_random_config = use_random_config
        self.browser_name = browser_name
        self.browser_version = browser_version
        self.console = Console()
        self._playwright = None
        self._camoufox = None
        self._solve_count = 0
        self._fail_count = 0
        
        # Initialize useragent and sec_ch_ua attributes
        self.useragent = useragent
        self.sec_ch_ua = None
        
        
        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            if browser_name and browser_version:
                config = browser_config.get_browser_config(browser_name, browser_version)
                if config:
                    useragent, sec_ch_ua = config
                    self.useragent = useragent
                    self.sec_ch_ua = sec_ch_ua
            elif useragent:
                self.useragent = useragent
            else:
                browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                self.browser_name = browser
                self.browser_version = version
                self.useragent = useragent
                self.sec_ch_ua = sec_ch_ua
        
        self.browser_args = []
        if self.useragent:
            self.browser_args.append(f"--user-agent={self.useragent}")

        self._setup_routes()

    def display_welcome(self):
        """Displays welcome screen with logo."""
        self.console.clear()
        
        combined_text = Text()
        combined_text.append("\n📢 Channel: ", style="bold white")
        combined_text.append("https://t.me/D3_vin", style="cyan")
        combined_text.append("\n💬 Chat: ", style="bold white")
        combined_text.append("https://t.me/D3vin_chat", style="cyan")
        combined_text.append("\n📁 GitHub: ", style="bold white")
        combined_text.append("https://github.com/D3-vin", style="cyan")
        combined_text.append("\n📁 Version: ", style="bold white")
        combined_text.append("1.2b", style="green")
        combined_text.append("\n")

        info_panel = Panel(
            Align.left(combined_text),
            title="[bold blue]Turnstile Solver[/bold blue]",
            subtitle="[bold magenta]Dev by D3vin[/bold magenta]",
            box=box.ROUNDED,
            border_style="bright_blue",
            padding=(0, 1),
            width=50
        )

        self.console.print(info_panel)
        self.console.print()




    def _setup_routes(self) -> None:
        """Set up the application routes."""
        self.app.before_serving(self._startup)
        self.app.route('/turnstile', methods=['GET'])(self.process_turnstile)
        self.app.route('/result', methods=['GET'])(self.get_result)
        self.app.route('/health', methods=['GET'])(self.health_check)
        self.app.route('/')(self.index)

    async def health_check(self):
        """Return a lightweight solver health summary."""
        pool_size = self.browser_pool.qsize()
        return jsonify({
            "status": "ok",
            "browser_type": self.browser_type,
            "headless": self.headless,
            "thread_count": self.thread_count,
            "pool_available": pool_size,
            "pool_utilization": f"{max(self.thread_count - pool_size, 0)}/{self.thread_count}",
            "solve_count": self._solve_count,
            "fail_count": self._fail_count,
        }), 200
        

    async def _startup(self) -> None:
        """Initialize the browser and page pool on startup."""
        self.display_welcome()
        logger.info("Starting browser initialization")
        try:
            await init_db()
            await self._initialize_browser()
            
            # Запускаем периодическую очистку старых результатов
            asyncio.create_task(self._periodic_cleanup())
            
        except Exception as e:
            logger.error(f"Failed to initialize browser: {str(e)}")
            raise

    async def _initialize_browser(self) -> None:
        """Initialize the browser and create the page pool."""
        memory_limit_raw = os.environ.get("SOLVER_MAX_MEMORY_MB", "").strip()
        if memory_limit_raw:
            try:
                memory_limit_mb = int(memory_limit_raw)
                if memory_limit_mb <= 512 and self.thread_count > 1:
                    self.thread_count = 1
                    logger.warning(f"Low memory limit ({memory_limit_mb}MB), reducing thread count to {self.thread_count}")
            except ValueError:
                logger.warning(f"Invalid SOLVER_MAX_MEMORY_MB value: {memory_limit_raw}")

        if self.browser_type in ['chromium', 'chrome', 'msedge'] and self._playwright is None:
            self._playwright = await async_playwright().start()
        elif self.browser_type == "camoufox" and self._camoufox is None:
            self._camoufox = AsyncCamoufox(headless=self.headless)

        browser_configs = []
        for _ in range(self.thread_count):
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                if self.use_random_config:
                    browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                elif self.browser_name and self.browser_version:
                    config = browser_config.get_browser_config(self.browser_name, self.browser_version)
                    if config:
                        useragent, sec_ch_ua = config
                        browser = self.browser_name
                        version = self.browser_version
                    else:
                        browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                else:
                    browser = getattr(self, 'browser_name', 'custom')
                    version = getattr(self, 'browser_version', 'custom')
                    useragent = self.useragent
                    sec_ch_ua = getattr(self, 'sec_ch_ua', '')
            else:
                # Для camoufox и других браузеров используем значения по умолчанию
                browser = self.browser_type
                version = 'custom'
                useragent = self.useragent
                sec_ch_ua = getattr(self, 'sec_ch_ua', '')

            
            browser_configs.append({
                'browser_name': browser,
                'browser_version': version,
                'useragent': useragent,
                'sec_ch_ua': sec_ch_ua
            })

        for i in range(self.thread_count):
            config = browser_configs[i]

            browser = await self._create_single_browser(i + 1, config)

            if browser:
                await self.browser_pool.put((i+1, browser, config))

            if self.debug:
                logger.info(f"Browser {i + 1} initialized successfully with {config['browser_name']} {config['browser_version']}")

        logger.info(f"Browser pool initialized with {self.browser_pool.qsize()} browsers")
        
        if self.use_random_config:
            logger.info(f"Each browser in pool received random configuration")
        elif self.browser_name and self.browser_version:
            logger.info(f"All browsers using configuration: {self.browser_name} {self.browser_version}")
        else:
            logger.info("Using custom configuration")
            
        if self.debug:
            for i, config in enumerate(browser_configs):
                logger.debug(f"Browser {i+1} config: {config['browser_name']} {config['browser_version']}")
                logger.debug(f"Browser {i+1} User-Agent: {config['useragent']}")
                logger.debug(f"Browser {i+1} Sec-CH-UA: {config['sec_ch_ua']}")

    async def _create_single_browser(self, index: int, config: dict):
        """Create a single browser instance for initialization or pool recovery."""
        browser_args = []
        if config.get('useragent'):
            browser_args.append(f"--user-agent={config['useragent']}")

        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            return await self._playwright.chromium.launch(
                channel=self.browser_type,
                headless=self.headless,
                args=browser_args
            )

        if self.browser_type == "camoufox":
            if self._camoufox is None:
                self._camoufox = AsyncCamoufox(headless=self.headless)
            return await self._camoufox.start()

        logger.warning(f"Browser {index}: Unsupported browser type {self.browser_type}")
        return None

    async def _periodic_cleanup(self):
        """Periodic cleanup of old results every hour"""
        cleanup_cycle = 0
        while True:
            try:
                await asyncio.sleep(300)
                stuck_count = await cleanup_stuck_tasks(timeout_minutes=5)
                if stuck_count > 0:
                    logger.info(f"Cleaned up {stuck_count} stuck processing tasks")

                cleanup_cycle = (cleanup_cycle + 1) % 12
                if cleanup_cycle == 0:
                    deleted_count = await cleanup_old_results(days_old=7)
                    if deleted_count > 0:
                        logger.info(f"Cleaned up {deleted_count} old results")
            except Exception as e:
                logger.error(f"Error during periodic cleanup: {e}")

    async def _antishadow_inject(self, page):
        await page.add_init_script("""
          (function() {
            const originalAttachShadow = Element.prototype.attachShadow;
            Element.prototype.attachShadow = function(init) {
              const shadow = originalAttachShadow.call(this, init);
              if (init.mode === 'closed') {
                window.__lastClosedShadowRoot = shadow;
              }
              return shadow;
            };
          })();
        """)

    async def _inject_before_load(self, page, index: int):
        """Install a pre-load hook that captures Turnstile callbacks before page scripts run."""
        try:
            await page.add_init_script("""
                (() => {
                    const wrapTurnstile = (target) => {
                        if (!target || typeof target.render !== 'function' || target.__solverRenderWrapped) {
                            return;
                        }

                        const originalRender = target.render;
                        target.render = function(container, params = {}) {
                            const nextParams = (params && typeof params === 'object') ? { ...params } : {};
                            const originalCallback = nextParams.callback;
                            nextParams.callback = function(token) {
                                window.__turnstile_token = token || '';
                                if (typeof originalCallback === 'function') {
                                    return originalCallback(token);
                                }
                            };

                            const originalErrorCallback = nextParams['error-callback'];
                            nextParams['error-callback'] = function(error) {
                                window.__turnstile_error = String(error || '');
                                if (typeof originalErrorCallback === 'function') {
                                    return originalErrorCallback(error);
                                }
                            };

                            return originalRender.call(this, container, nextParams);
                        };

                        target.__solverRenderWrapped = true;
                    };

                    window.__turnstile_token = '';
                    window.__turnstile_error = '';
                    window.__solverInstallTurnstileHook = () => wrapTurnstile(window.turnstile);

                    let currentTurnstile = window.turnstile;
                    if (!currentTurnstile || typeof currentTurnstile.render !== 'function') {
                        Object.defineProperty(window, 'turnstile', {
                            configurable: true,
                            enumerable: true,
                            get() {
                                return currentTurnstile;
                            },
                            set(value) {
                                currentTurnstile = value;
                                wrapTurnstile(currentTurnstile);
                            }
                        });
                    }

                    if (currentTurnstile) {
                        wrapTurnstile(currentTurnstile);
                    }
                })();
            """)
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: Failed to inject pre-load hook: {str(e)}")

    async def _install_post_load_hook(self, page, index: int):
        """Re-apply the Turnstile hook after load in case the pre-load hook was overwritten."""
        try:
            installed = await page.evaluate("""
                (() => {
                    if (typeof window.__solverInstallTurnstileHook === 'function') {
                        window.__solverInstallTurnstileHook();
                        return Boolean(window.turnstile && typeof window.turnstile.render === 'function');
                    }

                    if (!window.turnstile || typeof window.turnstile.render !== 'function') {
                        return false;
                    }

                    const originalRender = window.turnstile.render;
                    if (window.turnstile.__solverRenderWrapped) {
                        return true;
                    }

                    window.turnstile.render = function(container, params = {}) {
                        const nextParams = (params && typeof params === 'object') ? { ...params } : {};
                        const originalCallback = nextParams.callback;
                        nextParams.callback = function(token) {
                            window.__turnstile_token = token || '';
                            if (typeof originalCallback === 'function') {
                                return originalCallback(token);
                            }
                        };

                        const originalErrorCallback = nextParams['error-callback'];
                        nextParams['error-callback'] = function(error) {
                            window.__turnstile_error = String(error || '');
                            if (typeof originalErrorCallback === 'function') {
                                return originalErrorCallback(error);
                            }
                        };

                        return originalRender.call(this, container, nextParams);
                    };

                    window.turnstile.__solverRenderWrapped = true;
                    return true;
                })();
            """)
            if self.debug:
                logger.debug(f"Browser {index}: Post-load hook installed={installed}")
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: Failed to install post-load hook: {str(e)}")



    async def _optimized_route_handler(self, route):
        """Оптимизированный обработчик маршрутов для экономии ресурсов."""
        url = route.request.url
        resource_type = route.request.resource_type

        allowed_types = {'document', 'script', 'xhr', 'fetch', 'stylesheet', 'image'}

        allowed_domains = [
            'challenges.cloudflare.com',
            'static.cloudflareinsights.com',
            'cloudflare.com'
        ]
        
        if resource_type in allowed_types:
            await route.continue_()
        elif any(domain in url for domain in allowed_domains):
            await route.continue_() 
        else:
            await route.abort()

    async def _block_rendering(self, page):
        """Блокировка рендеринга для экономии ресурсов"""
        await page.route("**/*", self._optimized_route_handler)

    async def _unblock_rendering(self, page):
        """Разблокировка рендеринга"""
        await page.unroute("**/*", self._optimized_route_handler)

    async def _find_turnstile_elements(self, page, index: int):
        """Умная проверка всех возможных Turnstile элементов"""
        selectors = [
            '.cf-turnstile',
            '[data-sitekey]',
            'iframe[src*="turnstile"]',
            'iframe[title*="widget"]',
            'div[id*="turnstile"]',
            'div[class*="turnstile"]'
        ]
        
        elements = []
        for selector in selectors:
            try:
                # Безопасная проверка count()
                try:
                    count = await page.locator(selector).count()
                except Exception:
                    # Если count() дает ошибку, пропускаем этот селектор
                    continue
                    
                if count > 0:
                    elements.append((selector, count))
                    if self.debug:
                        logger.debug(f"Browser {index}: Found {count} elements with selector '{selector}'")
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Selector '{selector}' failed: {str(e)}")
                continue
        
        return elements

    async def _extract_sitekey(self, page, index: int) -> Optional[str]:
        """Try to auto-detect Turnstile sitekey from the loaded page."""
        try:
            candidates = await page.evaluate("""
                () => {
                    const values = [];
                    const seen = new Set();
                    const add = (value) => {
                        if (typeof value !== 'string') return;
                        const trimmed = value.trim();
                        if (!trimmed) return;
                        if (!seen.has(trimmed)) {
                            seen.add(trimmed);
                            values.push(trimmed);
                        }
                    };

                    document.querySelectorAll('[data-sitekey]').forEach((el) => add(el.getAttribute('data-sitekey')));

                    document.querySelectorAll('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"]').forEach((iframe) => {
                        try {
                            const src = iframe.getAttribute('src') || '';
                            const parsed = new URL(src, window.location.href);
                            add(parsed.searchParams.get('sitekey'));
                            add(parsed.searchParams.get('k'));
                        } catch (e) {}
                    });

                    const html = document.documentElement?.outerHTML || '';
                    const matches = html.match(/0x[a-zA-Z0-9_-]{10,}/g) || [];
                    matches.forEach(add);

                    return values;
                }
            """)

            if not candidates:
                return None

            for candidate in candidates:
                if isinstance(candidate, str) and candidate.startswith("0x"):
                    if self.debug:
                        logger.debug(f"Browser {index}: Auto-detected sitekey: {candidate}")
                    return candidate

            first_candidate = candidates[0] if isinstance(candidates, list) and candidates else None
            if isinstance(first_candidate, str) and first_candidate.strip():
                if self.debug:
                    logger.debug(f"Browser {index}: Auto-detected sitekey candidate: {first_candidate}")
                return first_candidate.strip()
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: Sitekey auto-detect failed: {str(e)}")
        return None

    async def _collect_page_diagnostics(self, page, index: int) -> str:
        """Collect page state useful for diagnosing missing Turnstile tokens."""
        try:
            info = await page.evaluate("""() => {
                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                const widget = document.querySelector('.cf-turnstile, [data-sitekey]');
                const input = document.querySelector('input[name="cf-turnstile-response"]');
                const inputValue = input ? input.value : '';
                return {
                    url: location.href.substring(0, 120),
                    title: document.title.substring(0, 60),
                    has_iframe: Boolean(iframe),
                    has_widget: Boolean(widget),
                    has_input: Boolean(input),
                    input_has_value: Boolean(inputValue),
                    input_value_preview: inputValue ? inputValue.substring(0, 20) + '...' : '',
                };
            }""")
            parts = [
                f"url={info.get('url', 'unknown')}",
                f"title={info.get('title', '')}",
                f"iframe={'YES' if info.get('has_iframe') else 'NO'}",
                f"widget={'YES' if info.get('has_widget') else 'NO'}",
                f"input={'YES' if info.get('has_input') else 'NO'}",
            ]
            if info.get('input_has_value'):
                parts.append(f"value={info.get('input_value_preview')}")
            return " ".join(parts)
        except Exception as e:
            return f"diag_error={str(e)}"

    async def _read_token_value(self, page, locator, index: int, attempt: int):
        """Read the first available token from the callback hook or response input."""
        try:
            intercepted = await page.evaluate("""() => ({
                token: typeof window.__turnstile_token === 'string' ? window.__turnstile_token : '',
                error: typeof window.__turnstile_error === 'string' ? window.__turnstile_error : ''
            })""")
            token = intercepted.get('token', '') if isinstance(intercepted, dict) else ''
            error = intercepted.get('error', '') if isinstance(intercepted, dict) else ''
            if token:
                return token, 0
            if error and self.debug and attempt % 5 == 0:
                logger.debug(f"Browser {index}: Turnstile callback error on attempt {attempt + 1}: {error}")
        except Exception as e:
            if self.debug and attempt == 0:
                logger.debug(f"Browser {index}: Intercepted token read failed: {str(e)}")

        try:
            count = await locator.count()
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: Locator count failed on attempt {attempt + 1}: {str(e)}")
            return None, 0

        if count <= 0:
            return None, 0

        if count == 1:
            try:
                return await locator.input_value(timeout=500), count
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Single token element check failed: {str(e)}")
                return None, count

        for token_index in range(count):
            try:
                token = await locator.nth(token_index).input_value(timeout=500)
                if token:
                    return token, count
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Token element {token_index} check failed: {str(e)}")
                continue

        return None, count

    async def _attempt_solve_round(self, page, locator, index: int, effective_sitekey: str, action: Optional[str], max_attempts: int, round_name: str):
        """Run one polling round with click and overlay fallbacks."""
        current_sitekey = effective_sitekey or ''
        overlay_attempt = min(10, max(3, max_attempts // 2))

        for attempt in range(max_attempts):
            token, count = await self._read_token_value(page, locator, index, attempt)
            if token:
                return current_sitekey, token

            if attempt % 5 == 0:
                diag = await self._collect_page_diagnostics(page, index)
                logger.info(f"Browser {index}: {round_name} attempt {attempt + 1}/{max_attempts} - {diag}")

            if attempt > 2 and attempt % 3 == 0:
                click_success = await self._try_click_strategies(page, index)
                if not click_success and self.debug:
                    logger.debug(f"Browser {index}: All click strategies failed on {round_name} attempt {attempt + 1}")

            if count == 0 and attempt == overlay_attempt:
                try:
                    if not current_sitekey:
                        current_sitekey = await self._extract_sitekey(page, index) or ''

                    if current_sitekey:
                        logger.info(f"Browser {index}: Injecting overlay fallback during {round_name} round")
                        await self._load_captcha_overlay(page, current_sitekey, action or '', index)
                        await asyncio.sleep(2)
                    elif self.debug:
                        logger.debug(f"Browser {index}: Skipping overlay fallback during {round_name} because sitekey is unavailable")
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Overlay fallback failed during {round_name}: {str(e)}")

            wait_time = min(0.5 + (attempt * 0.05), 2.0)
            await asyncio.sleep(wait_time)

        return current_sitekey, None

    async def _restore_browser_to_pool(self, index: int, browser, browser_config: dict):
        """Return a healthy browser to the pool or create a replacement."""
        connected = True
        try:
            if hasattr(browser, 'is_connected'):
                connected = browser.is_connected()
        except Exception as e:
            connected = False
            logger.warning(f"Browser {index}: Error checking browser connection: {str(e)}")

        if connected:
            await self.browser_pool.put((index, browser, browser_config))
            if self.debug:
                logger.debug(f"Browser {index}: Browser returned to pool")
            return

        logger.warning(f"Browser {index}: Disconnected, creating replacement")
        try:
            replacement = await self._create_single_browser(index, browser_config)
            if replacement:
                await self.browser_pool.put((index, replacement, browser_config))
                logger.info(f"Browser {index}: Replacement created successfully")
        except Exception as e:
            logger.error(f"Browser {index}: Failed to create replacement: {str(e)}")

    async def _find_and_click_checkbox(self, page, index: int):
        """Найти и кликнуть по чекбоксу Turnstile CAPTCHA внутри iframe"""
        try:
            # Пробуем разные селекторы iframe с защитой от ошибок
            iframe_selectors = [
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'iframe[title*="widget"]'
            ]
            
            iframe_locator = None
            for selector in iframe_selectors:
                try:
                    test_locator = page.locator(selector).first
                    # Безопасная проверка count для iframe
                    try:
                        iframe_count = await test_locator.count()
                    except Exception:
                        iframe_count = 0
                        
                    if iframe_count > 0:
                        iframe_locator = test_locator
                        if self.debug:
                            logger.debug(f"Browser {index}: Found Turnstile iframe with selector: {selector}")
                        break
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Iframe selector '{selector}' failed: {str(e)}")
                    continue
            
            if iframe_locator:
                try:
                    # Получаем frame из iframe
                    iframe_element = await iframe_locator.element_handle()
                    frame = await iframe_element.content_frame()
                    
                    if frame:
                        # Ищем чекбокс внутри iframe
                        checkbox_selectors = [
                            'input[type="checkbox"]',
                            '.cb-lb input[type="checkbox"]',
                            'label input[type="checkbox"]'
                        ]
                        
                        for selector in checkbox_selectors:
                            try:
                                # Полностью избегаем locator.count() в iframe - используем альтернативный подход
                                try:
                                    # Пробуем кликнуть напрямую без count проверки
                                    checkbox = frame.locator(selector).first
                                    await checkbox.click(timeout=2000)
                                    if self.debug:
                                        logger.debug(f"Browser {index}: Successfully clicked checkbox in iframe with selector '{selector}'")
                                    return True
                                except Exception as click_e:
                                    # Если прямой клик не сработал, записываем в debug но не падаем
                                    if self.debug:
                                        logger.debug(f"Browser {index}: Direct checkbox click failed for '{selector}': {str(click_e)}")
                                    continue
                            except Exception as e:
                                if self.debug:
                                    logger.debug(f"Browser {index}: Iframe checkbox selector '{selector}' failed: {str(e)}")
                                continue
                    
                        # Если нашли iframe, но не смогли кликнуть чекбокс, пробуем клик по iframe
                        try:
                            if self.debug:
                                logger.debug(f"Browser {index}: Trying to click iframe directly as fallback")
                            await iframe_locator.click(timeout=1000)
                            return True
                        except Exception as e:
                            if self.debug:
                                logger.debug(f"Browser {index}: Iframe direct click failed: {str(e)}")
                
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Failed to access iframe content: {str(e)}")
            
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: General iframe search failed: {str(e)}")
        
        return False

    async def _try_click_strategies(self, page, index: int):
        strategies = [
            ('checkbox_click', lambda: self._find_and_click_checkbox(page, index)),
            ('direct_widget', lambda: self._safe_click(page, '.cf-turnstile', index)),
            ('iframe_click', lambda: self._safe_click(page, 'iframe[src*="turnstile"]', index)),
            ('js_click', lambda: page.evaluate("document.querySelector('.cf-turnstile')?.click()")),
            ('sitekey_attr', lambda: self._safe_click(page, '[data-sitekey]', index)),
            ('any_turnstile', lambda: self._safe_click(page, '*[class*="turnstile"]', index)),
            ('xpath_click', lambda: self._safe_click(page, "//div[@class='cf-turnstile']", index))
        ]
        
        for strategy_name, strategy_func in strategies:
            try:
                result = await strategy_func()
                if result is True or result is None:  # None означает успех для большинства стратегий
                    if self.debug:
                        logger.debug(f"Browser {index}: Click strategy '{strategy_name}' succeeded")
                    return True
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Click strategy '{strategy_name}' failed: {str(e)}")
                continue
        
        return False

    async def _safe_click(self, page, selector: str, index: int):
        """Полностью безопасный клик с максимальной защитой от ошибок"""
        try:
            # Пробуем кликнуть напрямую без count() проверки
            locator = page.locator(selector).first
            await locator.click(timeout=1000)
            return True
        except Exception as e:
            # Логируем ошибку только в debug режиме
            if self.debug and "Can't query n-th element" not in str(e):
                logger.debug(f"Browser {index}: Safe click failed for '{selector}': {str(e)}")
            return False

    async def _load_captcha_overlay(self, page, websiteKey: str, action: str = '', index: int = 0):
        script = f"""
        const existing = document.querySelector('#captcha-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'captcha-overlay';
        overlay.style.position = 'absolute';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
        overlay.style.display = 'block';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '1000';

        const captchaDiv = document.createElement('div');
        captchaDiv.className = 'cf-turnstile';
        captchaDiv.setAttribute('data-sitekey', '{websiteKey}');
        captchaDiv.setAttribute('data-callback', 'onCaptchaSuccess');
        captchaDiv.setAttribute('data-action', '{action}');

        overlay.appendChild(captchaDiv);
        document.body.appendChild(overlay);

        const script = document.createElement('script');
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
        script.async = true;
        script.defer = true;
        document.head.appendChild(script);
        """

        await page.evaluate(script)
        if self.debug:
            logger.debug(f"Browser {index}: Created CAPTCHA overlay with sitekey: {websiteKey}")

    async def _solve_turnstile(self, task_id: str, url: str, sitekey: str, action: Optional[str] = None, cdata: Optional[str] = None):
        """Solve the Turnstile challenge."""
        proxy = None
        context = None
        page = None
        browser_returned = False

        index, browser, browser_config = await self.browser_pool.get()
        
        try:
            if hasattr(browser, 'is_connected') and not browser.is_connected():
                logger.warning(f"Browser {index}: Browser disconnected before solve start")
                browser_returned = True
                await self._restore_browser_to_pool(index, browser, browser_config)
                self._fail_count += 1
                await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": 0})
                return
        except Exception as e:
            logger.warning(f"Browser {index}: Cannot check browser state: {str(e)}")

        if self.proxy_support:
            proxy_file_path = os.path.join(os.getcwd(), "proxies.txt")

            try:
                with open(proxy_file_path) as proxy_file:
                    proxies = [line.strip() for line in proxy_file if line.strip()]

                proxy = random.choice(proxies) if proxies else None
                
                if self.debug and proxy:
                    logger.debug(f"Browser {index}: Selected proxy: {proxy}")
                elif self.debug and not proxy:
                    logger.debug(f"Browser {index}: No proxies available")
                    
            except FileNotFoundError:
                logger.warning(f"Proxy file not found: {proxy_file_path}")
                proxy = None
            except Exception as e:
                logger.error(f"Error reading proxy file: {str(e)}")
                proxy = None

            if proxy:
                if '@' in proxy:
                    try:
                        scheme_part, auth_part = proxy.split('://')
                        auth, address = auth_part.split('@')
                        username, password = auth.split(':')
                        ip, port = address.split(':')
                        if self.debug:
                            logger.debug(f"Browser {index}: Creating context with proxy {scheme_part}://{ip}:{port} (auth: {username}:***)")
                        context_options = {
                            "proxy": {
                                "server": f"{scheme_part}://{ip}:{port}",
                                "username": username,
                                "password": password
                            },
                            "user_agent": browser_config['useragent']
                        }
                        
                        if browser_config['sec_ch_ua'] and browser_config['sec_ch_ua'].strip():
                            context_options['extra_http_headers'] = {
                                'sec-ch-ua': browser_config['sec_ch_ua']
                            }
                        
                        context = await browser.new_context(**context_options)
                    except ValueError:
                        raise ValueError(f"Invalid proxy format: {proxy}")
                else:
                    parts = proxy.split(':')
                    if len(parts) == 5:
                        proxy_scheme, proxy_ip, proxy_port, proxy_user, proxy_pass = parts
                        if self.debug:
                            logger.debug(f"Browser {index}: Creating context with proxy {proxy_scheme}://{proxy_ip}:{proxy_port} (auth: {proxy_user}:***)")
                        context_options = {
                            "proxy": {
                                "server": f"{proxy_scheme}://{proxy_ip}:{proxy_port}",
                                "username": proxy_user,
                                "password": proxy_pass
                            },
                            "user_agent": browser_config['useragent']
                        }
                        
                        if browser_config['sec_ch_ua'] and browser_config['sec_ch_ua'].strip():
                            context_options['extra_http_headers'] = {
                                'sec-ch-ua': browser_config['sec_ch_ua']
                            }
                        
                        context = await browser.new_context(**context_options)
                    elif len(parts) == 3:
                        if self.debug:
                            logger.debug(f"Browser {index}: Creating context with proxy {proxy}")
                        context_options = {
                            "proxy": {"server": f"{proxy}"},
                            "user_agent": browser_config['useragent']
                        }
                        
                        if browser_config['sec_ch_ua'] and browser_config['sec_ch_ua'].strip():
                            context_options['extra_http_headers'] = {
                                'sec-ch-ua': browser_config['sec_ch_ua']
                            }
                        
                        context = await browser.new_context(**context_options)
                    else:
                        raise ValueError(f"Invalid proxy format: {proxy}")
            else:
                if self.debug:
                    logger.debug(f"Browser {index}: Creating context without proxy")
                context_options = {"user_agent": browser_config['useragent']}
                
                if browser_config['sec_ch_ua'] and browser_config['sec_ch_ua'].strip():
                    context_options['extra_http_headers'] = {
                        'sec-ch-ua': browser_config['sec_ch_ua']
                    }
                
                context = await browser.new_context(**context_options)
        else:
            context_options = {"user_agent": browser_config['useragent']}
            
            if browser_config['sec_ch_ua'] and browser_config['sec_ch_ua'].strip():
                context_options['extra_http_headers'] = {
                    'sec-ch-ua': browser_config['sec_ch_ua']
                }
            
            context = await browser.new_context(**context_options)

        page = await context.new_page()
        
        #await self._antishadow_inject(page)
        
        await self._block_rendering(page)
        await self._inject_before_load(page, index)

        #await page.add_init_script("""
        #Object.defineProperty(navigator, 'webdriver', {
        #    get: () => undefined,
        #});
        
        #window.chrome = {
        #    runtime: {},
        #    loadTimes: function() {},
        #    csi: function() {},
        #};
        ##""")
        
        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            await page.set_viewport_size({"width": 500, "height": 200})
            if self.debug:
                logger.debug(f"Browser {index}: Set viewport size to 500x200")

        start_time = time.time()

        try:
            if self.debug:
                logger.debug(f"Browser {index}: Starting Turnstile solve for URL: {url} with Sitekey: {sitekey} | Action: {action} | Cdata: {cdata} | Proxy: {proxy}")
                logger.debug(f"Browser {index}: Setting up optimized page loading with resource blocking")

            if self.debug:
                logger.debug(f"Browser {index}: Loading real website directly: {url}")

            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await self._unblock_rendering(page)
            await self._install_post_load_hook(page, index)

            try:
                page_title = await page.title()
            except Exception:
                page_title = ""
            logger.info(f"Browser {index}: Page loaded, URL: {page.url}, title: {page_title[:80]}")

            # Check if Cloudflare script loaded
            try:
                has_cf_script = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('script')).some(
                        s => s.src && s.src.includes('challenges.cloudflare.com')
                    );
                }""")
                logger.info(f"Browser {index}: Cloudflare script present: {has_cf_script}")
            except Exception:
                pass

            # Ждем немного времени для загрузки CAPTCHA
            await asyncio.sleep(3)

            effective_sitekey = (sitekey or '').strip()
            if not effective_sitekey:
                detected_sitekey = await self._extract_sitekey(page, index)
                if detected_sitekey:
                    effective_sitekey = detected_sitekey
                    if self.debug:
                        logger.debug(f"Browser {index}: Using auto-detected sitekey: {effective_sitekey}")

            locator = page.locator('input[name="cf-turnstile-response"]')
            effective_sitekey, token = await self._attempt_solve_round(
                page,
                locator,
                index,
                effective_sitekey,
                action,
                20,
                "primary"
            )

            if not token:
                logger.info(f"Browser {index}: First round failed, refreshing page for retry")
                await self._block_rendering(page)
                await self._inject_before_load(page, index)
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await self._unblock_rendering(page)
                await self._install_post_load_hook(page, index)
                await asyncio.sleep(3)

                try:
                    retry_title = await page.title()
                except Exception:
                    retry_title = ""
                logger.info(f"Browser {index}: Retry page loaded, URL: {page.url}, title: {retry_title[:80]}")

                locator = page.locator('input[name=\"cf-turnstile-response\"]')
                effective_sitekey, token = await self._attempt_solve_round(
                    page,
                    locator,
                    index,
                    effective_sitekey,
                    action,
                    10,
                    "retry"
                )

            if token:
                elapsed_time = round(time.time() - start_time, 3)
                self._solve_count += 1
                logger.success(f"Browser {index}: Successfully solved captcha - {COLORS.get('MAGENTA')}{token[:10]}{COLORS.get('RESET')} in {COLORS.get('GREEN')}{elapsed_time}{COLORS.get('RESET')} Seconds")
                await save_result(task_id, "turnstile", {"value": token, "elapsed_time": elapsed_time})
                return

            elapsed_time = round(time.time() - start_time, 3)
            self._fail_count += 1
            await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": elapsed_time})
            logger.error(f"Browser {index}: Failed to solve Turnstile in {COLORS.get('RED')}{elapsed_time}{COLORS.get('RESET')} Seconds")
            return
        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            self._fail_count += 1
            await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": elapsed_time})
            logger.error(f"Browser {index}: Error solving Turnstile: {str(e)}")
        finally:
            if self.debug:
                logger.debug(f"Browser {index}: Closing browser context and cleaning up")
            
            try:
                if context is not None:
                    await context.close()
                    if self.debug:
                        logger.debug(f"Browser {index}: Context closed successfully")
            except Exception as e:
                if self.debug:
                    logger.warning(f"Browser {index}: Error closing context: {str(e)}")
            
            if not browser_returned:
                try:
                    await self._restore_browser_to_pool(index, browser, browser_config)
                    browser_returned = True
                except Exception as e:
                    logger.warning(f"Browser {index}: Error returning browser to pool: {str(e)}")






    async def process_turnstile(self):
        """Handle the /turnstile endpoint requests."""
        url = request.args.get('url')
        sitekey = request.args.get('sitekey')
        action = request.args.get('action')
        cdata = request.args.get('cdata')

        if not url:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_WRONG_PAGEURL",
                "errorDescription": "'url' is required"
            }), 200

        task_id = str(uuid.uuid4())
        await save_result(task_id, "turnstile", {
            "status": "CAPTCHA_NOT_READY",
            "createTime": int(time.time()),
            "url": url,
            "sitekey": sitekey,
            "action": action,
            "cdata": cdata
        })

        try:
            asyncio.create_task(self._solve_turnstile(task_id=task_id, url=url, sitekey=sitekey, action=action, cdata=cdata))

            if self.debug:
                logger.debug(f"Request completed with taskid {task_id}.")
            return jsonify({
                "errorId": 0,
                "taskId": task_id
            }), 200
        except Exception as e:
            logger.error(f"Unexpected error processing request: {str(e)}")
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_UNKNOWN",
                "errorDescription": str(e)
            }), 200

    async def get_result(self):
        """Return solved data"""
        task_id = request.args.get('id')

        if not task_id:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_WRONG_CAPTCHA_ID",
                "errorDescription": "Invalid task ID/Request parameter"
            }), 200

        result = await load_result(task_id)
        if not result:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": "Task not found"
            }), 200

        if result == "CAPTCHA_NOT_READY" or (isinstance(result, dict) and result.get("status") == "CAPTCHA_NOT_READY"):
            return jsonify({"status": "processing"}), 200

        if isinstance(result, dict) and result.get("value") == "CAPTCHA_FAIL":
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": "Workers could not solve the Captcha"
            }), 200

        if isinstance(result, dict) and result.get("value") and result.get("value") != "CAPTCHA_FAIL":
            return jsonify({
                "errorId": 0,
                "status": "ready",
                "solution": {
                    "token": result["value"]
                }
            }), 200
        else:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": "Workers could not solve the Captcha"
            }), 200

    

    @staticmethod
    async def index():
        """Serve the API documentation page."""
        return """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Turnstile Solver API</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-gray-900 text-gray-200 min-h-screen flex items-center justify-center">
                <div class="bg-gray-800 p-8 rounded-lg shadow-md max-w-2xl w-full border border-red-500">
                    <h1 class="text-3xl font-bold mb-6 text-center text-red-500">Welcome to Turnstile Solver API</h1>

                    <p class="mb-4 text-gray-300">To use the turnstile service, send a GET request to 
                       <code class="bg-red-700 text-white px-2 py-1 rounded">/turnstile</code> with the following query parameters:</p>

                    <ul class="list-disc pl-6 mb-6 text-gray-300">
                        <li><strong>url</strong>: The URL where Turnstile is to be validated</li>
                        <li><strong>sitekey</strong>: Optional. If omitted, the service will try to auto-detect it from the page</li>
                    </ul>

                    <div class="bg-gray-700 p-4 rounded-lg mb-6 border border-red-500">
                        <p class="font-semibold mb-2 text-red-400">Example usage:</p>
                        <code class="text-sm break-all text-red-300">/turnstile?url=https://example.com</code>
                    </div>


                    <div class="bg-gray-700 p-4 rounded-lg mb-6">
                        <p class="text-gray-200 font-semibold mb-3">📢 Connect with Us</p>
                        <div class="space-y-2 text-sm">
                            <p class="text-gray-300">
                                📢 <strong>Channel:</strong> 
                                <a href="https://t.me/D3_vin" class="text-red-300 hover:underline">https://t.me/D3_vin</a> 
                                - Latest updates and releases
                            </p>
                            <p class="text-gray-300">
                                💬 <strong>Chat:</strong> 
                                <a href="https://t.me/D3vin_chat" class="text-red-300 hover:underline">https://t.me/D3vin_chat</a> 
                                - Community support and discussions
                            </p>
                            <p class="text-gray-300">
                                📁 <strong>GitHub:</strong> 
                                <a href="https://github.com/D3-vin" class="text-red-300 hover:underline">https://github.com/D3-vin</a> 
                                - Source code and development
                            </p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
        """


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Turnstile API Server")

    parser.add_argument('--no-headless', action='store_true', help='Run the browser with GUI (disable headless mode). By default, headless mode is enabled.')
    parser.add_argument('--useragent', type=str, help='User-Agent string (if not specified, random configuration is used)')
    parser.add_argument('--debug', action='store_true', help='Enable or disable debug mode for additional logging and troubleshooting information (default: False)')
    parser.add_argument('--browser_type', type=str, default='chromium', help='Specify the browser type for the solver. Supported options: chromium, chrome, msedge, camoufox (default: chromium)')
    parser.add_argument('--thread', type=int, default=4, help='Set the number of browser threads to use for multi-threaded mode. Increasing this will speed up execution but requires more resources (default: 1)')
    parser.add_argument('--proxy', action='store_true', help='Enable proxy support for the solver (Default: False)')
    parser.add_argument('--random', action='store_true', help='Use random User-Agent and Sec-CH-UA configuration from pool')
    parser.add_argument('--browser', type=str, help='Specify browser name to use (e.g., chrome, firefox)')
    parser.add_argument('--version', type=str, help='Specify browser version to use (e.g., 139, 141)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Specify the IP address where the API solver runs. (Default: 127.0.0.1)')
    parser.add_argument('--port', type=str, default='5072', help='Set the port for the API solver to listen on. (Default: 5072)')
    return parser.parse_args()


def create_app(headless: bool, useragent: str, debug: bool, browser_type: str, thread: int, proxy_support: bool, use_random_config: bool, browser_name: str, browser_version: str) -> Quart:
    server = TurnstileAPIServer(headless=headless, useragent=useragent, debug=debug, browser_type=browser_type, thread=thread, proxy_support=proxy_support, use_random_config=use_random_config, browser_name=browser_name, browser_version=browser_version)
    return server.app


if __name__ == '__main__':
    args = parse_args()
    browser_types = [
        'chromium',
        'chrome',
        'msedge',
        'camoufox',
    ]
    if args.browser_type not in browser_types:
        logger.error(f"Unknown browser type: {COLORS.get('RED')}{args.browser_type}{COLORS.get('RESET')} Available browser types: {browser_types}")
    else:
        app = create_app(
            headless=not args.no_headless, 
            debug=args.debug, 
            useragent=args.useragent, 
            browser_type=args.browser_type, 
            thread=args.thread, 
            proxy_support=args.proxy,
            use_random_config=args.random,
            browser_name=args.browser,
            browser_version=args.version
        )
        app.run(host=args.host, port=int(args.port))
