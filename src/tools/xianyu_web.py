"""
闲鱼网页版 Playwright 客户端
基于 goofish.com 实现：搜索商品、获取详情、发布商品、管理订单
"""

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Cookie


class XianyuWebClient:
    """闲鱼网页版自动化客户端"""

    BASE_URL = "https://www.goofish.com"

    def __init__(self, account_name: str, cookie_file: str):
        self.account_name = account_name
        self.cookie_file = cookie_file
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def init(self):
        """初始化浏览器，加载 Cookie"""
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        # 加载 Cookie
        cookies = await self._load_cookies()
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info(f"[{self.account_name}] 已加载 {len(cookies)} 条 Cookie")
        else:
            logger.warning(f"[{self.account_name}] 无 Cookie 文件，需先登录导出")

        self._page = await self._context.new_page()
        # 访问首页验证登录状态
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)

    async def _load_cookies(self) -> list[dict]:
        """从 JSON 文件加载 Cookie"""
        path = Path(self.cookie_file)
        if not path.exists():
            logger.warning(f"Cookie 文件不存在: {path}")
            return []
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 适配多种 Cookie 格式
        cookies = []
        for item in raw:
            cookie = {
                "name": item.get("name", ""),
                "value": item.get("value", ""),
                "domain": item.get("domain", ".goofish.com"),
                "path": item.get("path", "/"),
            }
            if item.get("expires"):
                cookie["expires"] = item["expires"]
            if item.get("httpOnly"):
                cookie["httpOnly"] = item["httpOnly"]
            if item.get("secure"):
                cookie["secure"] = item["secure"]
            if item.get("sameSite"):
                cookie["sameSite"] = item["sameSite"]
            cookies.append(cookie)
        return cookies

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _human_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """模拟人类操作延时"""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    # ========== 搜索商品 ==========

    async def search_items(self, keyword: str, page_num: int = 1, limit: int = 30) -> list[dict]:
        """
        搜索闲鱼商品（比价/选品用）
        返回: [{item_id, title, price, seller, location, url}, ...]
        """
        logger.info(f"[{self.account_name}] 搜索: {keyword} (第{page_num}页)")
        try:
            url = f"{self.BASE_URL}/search?q={keyword}&page={page_num}"
            await self._page.goto(url, wait_until="domcontentloaded")
            await self._human_delay(2, 4)

            # 等待商品列表加载
            items = await self._page.evaluate("""
                () => {
                    const results = [];
                    // 闲鱼网页版搜索结果选择器（可能随版本变化，需维护）
                    const cards = document.querySelectorAll('[class*="item"], [class*="card"], [class*="product"]');
                    cards.forEach(card => {
                        const titleEl = card.querySelector('[class*="title"], h3, h4');
                        const priceEl = card.querySelector('[class*="price"]');
                        const linkEl = card.querySelector('a[href*="/item/"]');
                        if (titleEl && priceEl) {
                            results.push({
                                item_id: linkEl ? linkEl.href.match(/\\/item\\/(\\d+)/)?.[1] || '' : '',
                                title: titleEl.textContent.trim(),
                                price: parseFloat(priceEl.textContent.replace(/[^0-9.]/g, '')) || 0,
                                url: linkEl ? linkEl.href : '',
                            });
                        }
                    });
                    return results;
                }
            """)
            logger.info(f"[{self.account_name}] 搜索到 {len(items)} 条结果")
            return items[:limit]
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    # ========== 商品详情 ==========

    async def get_item_detail(self, item_id: str) -> dict:
        """获取商品详情"""
        logger.info(f"[{self.account_name}] 获取商品详情: {item_id}")
        try:
            url = f"{self.BASE_URL}/item/{item_id}"
            await self._page.goto(url, wait_until="domcontentloaded")
            await self._human_delay(2, 3)

            detail = await self._page.evaluate("""
                () => {
                    const getText = (sel) => {
                        const el = document.querySelector(sel);
                        return el ? el.textContent.trim() : '';
                    };
                    return {
                        title: getText('[class*="title"], h1'),
                        price: parseFloat(getText('[class*="price"]').replace(/[^0-9.]/g, '')) || 0,
                        description: getText('[class*="desc"], [class*="content"]'),
                        seller: getText('[class*="seller"], [class*="user"]'),
                    };
                }
            """)
            detail["item_id"] = item_id
            detail["url"] = url
            return detail
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
            return {"item_id": item_id, "error": str(e)}

    # ========== 发布商品 ==========

    async def publish_item(self, title: str, description: str, price: float,
                           images: list[str] | None = None,
                           category: str = "", shipping: str = "包邮") -> dict:
        """
        发布商品到闲鱼
        images: 本地图片路径列表
        返回: {success, item_id, error}
        """
        logger.info(f"[{self.account_name}] 发布商品: {title} (¥{price})")
        try:
            # 导航到发布页
            await self._page.goto(f"{self.BASE_URL}/publish", wait_until="domcontentloaded")
            await self._human_delay(2, 4)

            # 上传图片
            if images:
                file_input = await self._page.query_selector('input[type="file"]')
                if file_input:
                    await file_input.set_input_files(images[:5])  # 最多5张
                    await self._human_delay(3, 5)

            # 填写标题
            title_input = await self._page.query_selector('[placeholder*="标题"], [class*="title"] input, [class*="title"] textarea')
            if title_input:
                await title_input.fill(title)
                await self._human_delay(1, 2)

            # 填写价格
            price_input = await self._page.query_selector('[placeholder*="价格"], [class*="price"] input')
            if price_input:
                await price_input.fill(str(int(price)))
                await self._human_delay(1, 2)

            # 填写描述
            desc_input = await self._page.query_selector('[placeholder*="描述"], [class*="desc"] textarea')
            if desc_input:
                await desc_input.fill(description)
                await self._human_delay(1, 2)

            # 点击发布按钮
            publish_btn = await self._page.query_selector('button:has-text("发布"), [class*="publish"] button')
            if publish_btn:
                await publish_btn.click()
                await self._human_delay(3, 5)

                # 检查发布结果
                current_url = self._page.url
                if "/item/" in current_url:
                    item_id = current_url.split("/item/")[-1].split("?")[0]
                    logger.info(f"发布成功: {item_id}")
                    return {"success": True, "item_id": item_id}

            logger.warning("发布结果不确定，未检测到商品ID")
            return {"success": False, "error": "发布后未跳转到商品页"}
        except Exception as e:
            logger.error(f"发布失败: {e}")
            return {"success": False, "error": str(e)}

    # ========== 擦亮商品（维护曝光） ==========

    async def polish_item(self, item_id: str) -> bool:
        """擦亮单个商品（点击"擦亮"按钮刷新曝光）"""
        logger.info(f"[{self.account_name}] 擦亮商品: {item_id}")
        try:
            # 进入"我的闲鱼"管理页
            await self._page.goto(f"{self.BASE_URL}/my", wait_until="domcontentloaded")
            await self._human_delay(2, 3)

            # 查找擦亮按钮并点击（选择器可能随版本变化）
            polish_btn = await self._page.query_selector(f'button:has-text("擦亮")')
            if polish_btn:
                await polish_btn.click()
                await self._human_delay(1, 2)
                logger.info(f"擦亮成功: {item_id}")
                return True
            logger.warning(f"未找到擦亮按钮: {item_id}")
            return False
        except Exception as e:
            logger.error(f"擦亮失败: {e}")
            return False

    # ========== 订单管理 ==========

    async def get_orders(self, status: str = "all") -> list[dict]:
        """
        获取订单列表
        status: all / pending / paid / shipped / completed
        """
        logger.info(f"[{self.account_name}] 获取订单列表: {status}")
        try:
            await self._page.goto(f"{self.BASE_URL}/my/orders", wait_until="domcontentloaded")
            await self._human_delay(2, 3)

            orders = await self._page.evaluate("""
                (status) => {
                    const results = [];
                    const orderCards = document.querySelectorAll('[class*="order"], [class*="trade"]');
                    orderCards.forEach(card => {
                        const idEl = card.querySelector('[class*="order-id"], [class*="trade-id"]');
                        const titleEl = card.querySelector('[class*="title"], h3');
                        const priceEl = card.querySelector('[class*="price"]');
                        const statusEl = card.querySelector('[class*="status"]');
                        if (idEl) {
                            results.push({
                                order_id: idEl.textContent.trim(),
                                item_title: titleEl ? titleEl.textContent.trim() : '',
                                price: parseFloat(priceEl?.textContent?.replace(/[^0-9.]/g, '') || '0'),
                                status: statusEl ? statusEl.textContent.trim() : '',
                            });
                        }
                    });
                    return results;
                }
            """, status)
            return orders
        except Exception as e:
            logger.error(f"获取订单失败: {e}")
            return []

    async def fill_tracking_number(self, order_id: str, tracking_no: str, company: str = "中通快递") -> bool:
        """填写快递单号（发货）"""
        logger.info(f"[{self.account_name}] 填写快递单号: 订单{order_id} → {tracking_no}")
        try:
            # 进入订单详情
            await self._page.goto(f"{self.BASE_URL}/order/{order_id}", wait_until="domcontentloaded")
            await self._human_delay(2, 3)

            # 点击发货
            ship_btn = await self._page.query_selector('button:has-text("发货"), [class*="ship"] button')
            if ship_btn:
                await ship_btn.click()
                await self._human_delay(1, 2)

            # 填写单号
            tracking_input = await self._page.query_selector('[placeholder*="单号"], [class*="tracking"] input')
            if tracking_input:
                await tracking_input.fill(tracking_no)
                await self._human_delay(0.5, 1)

            # 确认发货
            confirm_btn = await self._page.query_selector('button:has-text("确认"), button:has-text("提交")')
            if confirm_btn:
                await confirm_btn.click()
                await self._human_delay(2, 3)
                logger.info(f"发货成功: {order_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"填写单号失败: {e}")
            return False

    # ========== Cookie 导出（辅助工具） ==========

    async def export_cookies(self, output_file: str):
        """导出当前浏览器 Cookie（用于首次登录后保存）"""
        if not self._context:
            logger.error("浏览器未初始化")
            return
        cookies = await self._context.cookies()
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"Cookie 已导出到 {path} ({len(cookies)} 条)")


async def interactive_login(account_name: str, cookie_file: str):
    """
    交互式登录（首次使用时手动扫码登录，然后导出 Cookie）
    会打开有头浏览器，等待用户扫码登录闲鱼
    """
    logger.info(f"启动交互式登录: {account_name}")
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()
        await page.goto("https://www.goofish.com")
        logger.info("请在浏览器中扫码登录闲鱼，登录成功后按回车继续...")
        input("登录完成后按回车键...")

        # 导出 Cookie
        cookies = await context.cookies()
        path = Path(cookie_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"Cookie 已保存到 {path}")
        await browser.close()
