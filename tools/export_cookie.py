"""
闲鱼 Cookie 导出工具

提供三种导出方式：
1. Playwright 交互式登录（推荐，自动导出）
2. 浏览器控制台脚本（手动复制）
3. Chrome 扩展（一键导出）

使用方法见 README 或运行: python tools/export_cookie.py --help
"""

import asyncio
import json
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ========== 方式1: Playwright 交互式登录 ==========

async def playwright_login(cookie_file: str = "data/cookies/account1.json"):
    """
    打开浏览器，手动扫码登录闲鱼，自动导出 Cookie
    需要安装: pip install playwright && playwright install chromium
    """
    from playwright.async_api import async_playwright

    print("\n" + "=" * 60)
    print("  闲鱼 Cookie 导出工具 (Playwright)")
    print("=" * 60)
    print(f"  Cookie 保存位置: {cookie_file}")
    print("  步骤:")
    print("  1. 浏览器将打开闲鱼登录页")
    print("  2. 用手机淘宝/闲鱼 App 扫码登录")
    print("  3. 登录成功后回到终端按回车")
    print("  4. Cookie 自动保存")
    print("=" * 60 + "\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()
        await page.goto("https://www.goofish.com")

        # 等待用户登录
        input(">>> 登录成功后，按回车键继续...")

        # 导出 Cookie
        cookies = await context.cookies()
        output = Path(PROJECT_ROOT / cookie_file)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        print(f"\n✅ Cookie 已保存到 {output}")
        print(f"   共 {len(cookies)} 条 Cookie")
        print(f"   关键 Cookie 检查:")
        key_cookies = ["_m_h5_tk", "cookie2", "unb", "sgcookie"]
        for key in key_cookies:
            found = any(c["name"] == key for c in cookies)
            status = "✅" if found else "❌"
            print(f"   {status} {key}")

        await browser.close()

    print("\n✅ 导出完成！请将 Cookie 文件路径填入 config/settings.local.yaml")


# ========== 方式2: 浏览器控制台脚本 ==========

CONSOLE_SCRIPT = """
// 在浏览器中打开 https://www.goofish.com 并登录后
// 按 F12 打开控制台，粘贴以下代码并回车：

(async function() {
    // 获取所有 Cookie
    const cookies = await cookieStore.getAll();
    // 格式化为 Playwright 兼容格式
    const formatted = cookies.map(c => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        expires: c.expires,
        httpOnly: c.httpOnly || false,
        secure: c.secure,
        sameSite: c.sameSite || 'Lax',
    }));
    // 下载为 JSON 文件
    const blob = new Blob([JSON.stringify(formatted, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'account1.json';
    a.click();
    console.log(`✅ 已导出 ${formatted.length} 条 Cookie`);
    console.log('请将下载的 account1.json 放到 data/cookies/ 目录');
})();
"""


# ========== 方式3: Chrome 扩展（一键导出） ==========

CHROME_EXTENSION = """
Chrome 扩展方式（适合不熟悉控制台的用户）：

1. 安装 "Cookie-Editor" 扏 Chrome 扩展
2. 打开 https://www.goofish.com 并登录
3. 点击 Cookie-Editor 扩展图标
4. 选择 "Export" → "Export as JSON"
5. 保存文件为 account1.json
6. 将文件放到项目的 data/cookies/ 目录
"""


def print_help():
    print("\n闲鱼 Cookie 导出工具")
    print("=" * 60)
    print("\n方式1（推荐）: Playwright 自动登录导出")
    print("  python tools/export_cookie.py --method playwright")
    print("  → 打开浏览器扫码登录，自动导出 Cookie")
    print("\n方式2: 浏览器控制台手动导出")
    print("  python tools/export_cookie.py --method console")
    print("  → 打印控制台脚本，复制到浏览器执行")
    print("\n方式3: Chrome 扩展导出")
    print("  python tools/export_cookie.py --method extension")
    print("  → 显示 Chrome 扩展使用说明")
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="闲鱼 Cookie 导出工具")
    parser.add_argument("--method", choices=["playwright", "console", "extension"],
                        default="playwright", help="导出方式")
    parser.add_argument("--output", default="data/cookies/account1.json",
                        help="Cookie 保存路径")
    args = parser.parse_args()

    if args.method == "playwright":
        asyncio.run(playwright_login(args.output))
    elif args.method == "console":
        print("\n在浏览器中打开 https://www.goofish.com 并登录后，")
        print("按 F12 打开控制台，粘贴以下代码并回车：\n")
        print(CONSOLE_SCRIPT)
    elif args.method == "extension":
        print(CHROME_EXTENSION)


if __name__ == "__main__":
    main()
