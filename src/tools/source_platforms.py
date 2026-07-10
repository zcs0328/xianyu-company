"""
货源平台工具：拼多多 / 1688 低价货源搜索
从上游批发平台检索低价商品，作为闲鱼无货源倒卖的货源端
"""

import asyncio
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保 src 模块可被导入（直接运行本文件时生效）
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import httpx
from loguru import logger

from src.config import get_config, PROJECT_ROOT


# ========== 数据模型 ==========

@dataclass
class SourceProduct:
    """货源商品数据模型"""
    product_id: str          # 货源平台商品ID
    title: str               # 商品标题
    price: float             # 采购单价（元）
    source_url: str          # 货源平台商品链接
    image_urls: list[str]    # 商品主图列表
    platform: str            # 来源平台：pinduoduo / alibaba1688
    shop_name: str           # 店铺名称
    sales: int               # 销量（件）
    location: str            # 发货地

    def to_dict(self) -> dict[str, Any]:
        """转为字典（便于日志/落库）"""
        return {
            "product_id": self.product_id,
            "title": self.title,
            "price": self.price,
            "source_url": self.source_url,
            "image_urls": self.image_urls,
            "platform": self.platform,
            "shop_name": self.shop_name,
            "sales": self.sales,
            "location": self.location,
        }


# ========== 模拟数据（演示模式 / DEMO） ==========

# 拼多多模拟商品（厨房收纳、家居用品类）
_PINDDUODUO_DEMO: list[dict[str, Any]] = [
    {
        "product_id": "PDD_190023456001",
        "title": "透明亚克力调料盒套装6格 厨房收纳盒防潮带盖",
        "price": 8.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456001",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-04/demo-tiaoliao-1.jpg",
            "https://img.pddpic.com/goods/images/2023-04/demo-tiaoliao-2.jpg",
        ],
        "shop_name": "收纳优品家居专营店",
        "sales": 83200,
        "location": "浙江 金华",
    },
    {
        "product_id": "PDD_190023456002",
        "title": "厨房不锈钢置物架多层落地式 微波炉烤箱收纳架",
        "price": 36.80,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456002",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-05/demo-zhiwu-1.jpg",
            "https://img.pddpic.com/goods/images/2023-05/demo-zhiwu-2.jpg",
        ],
        "shop_name": "欧派家居生活馆",
        "sales": 41500,
        "location": "广东 佛山",
    },
    {
        "product_id": "PDD_190023456003",
        "title": "冰箱收纳盒透明带盖保鲜盒 食品级特大号储物盒",
        "price": 5.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456003",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-06/demo-bingxiang-1.jpg",
            "https://img.pddpic.com/goods/images/2023-06/demo-bingxiang-2.jpg",
        ],
        "shop_name": "保鲜生活旗舰店",
        "sales": 156800,
        "location": "山东 临沂",
    },
    {
        "product_id": "PDD_190023456004",
        "title": "抽屉分隔收纳盒可调节 厨房桌面杂物整理盒",
        "price": 4.50,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456004",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-03/demo-chouti-1.jpg",
        ],
        "shop_name": "井井有理收纳旗舰店",
        "sales": 67400,
        "location": "浙江 台州",
    },
    {
        "product_id": "PDD_190023456005",
        "title": "免打孔壁挂厨房挂架 不锈钢刀架铲勺挂钩置物架",
        "price": 12.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456005",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-07/demo-guajia-1.jpg",
            "https://img.pddpic.com/goods/images/2023-07/demo-guajia-2.jpg",
        ],
        "shop_name": "厨卫优品专卖店",
        "sales": 29300,
        "location": "广东 广州",
    },
    {
        "product_id": "PDD_190023456006",
        "title": "玻璃密封罐储粮罐 厨房干货杂粮收纳罐带盖",
        "price": 6.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456006",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-02/demo-mifengguan-1.jpg",
            "https://img.pddpic.com/goods/images/2023-02/demo-mifengguan-2.jpg",
        ],
        "shop_name": "乐扣家居生活馆",
        "sales": 52100,
        "location": "江苏 徐州",
    },
    {
        "product_id": "PDD_190023456007",
        "title": "厨房沥水架碗碟架不锈钢 洗碗池旁置物架沥干架",
        "price": 19.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456007",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-08/demo-loushui-1.jpg",
        ],
        "shop_name": "厨美家居旗舰店",
        "sales": 38900,
        "location": "广东 揭阳",
    },
    {
        "product_id": "PDD_190023456008",
        "title": "360度旋转收纳盘 厨房桌面调料转盘化妆品收纳",
        "price": 7.80,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456008",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-01/demo-zhuanpan-1.jpg",
        ],
        "shop_name": "懒人家居日用店",
        "sales": 74600,
        "location": "浙江 义乌",
    },
    {
        "product_id": "PDD_190023456009",
        "title": "水槽下收纳架可伸缩 厨房橱柜下水管旁置物架",
        "price": 15.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456009",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-09/demo-shuicao-1.jpg",
            "https://img.pddpic.com/goods/images/2023-09/demo-shuicao-2.jpg",
        ],
        "shop_name": "空间魔法收纳店",
        "sales": 18800,
        "location": "福建 泉州",
    },
    {
        "product_id": "PDD_190023456010",
        "title": "厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架",
        "price": 3.90,
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=190023456010",
        "image_urls": [
            "https://img.pddpic.com/goods/images/2023-10/demo-lajidai-1.jpg",
        ],
        "shop_name": "九块九包邮精选店",
        "sales": 234500,
        "location": "浙江 义乌",
    },
]

# 1688模拟商品（厨房收纳、家居用品类，批发价更低）
_ALIBABA1688_DEMO: list[dict[str, Any]] = [
    {
        "product_id": "1688_660123456001",
        "title": "厂家直供 亚克力调料盒6格套装 防潮厨房收纳盒批发",
        "price": 5.50,
        "source_url": "https://detail.1688.com/offer/660123456001.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-tiaoliao-1.jpg",
            "https://cbu01.alicdn.com/img/ibank/2023/demo-tiaoliao-2.jpg",
        ],
        "shop_name": "义乌市收纳家居源头工厂",
        "sales": 312000,
        "location": "浙江 金华",
    },
    {
        "product_id": "1688_660123456002",
        "title": "不锈钢厨房置物架多层落地式 工厂直销烤箱微波炉收纳架",
        "price": 28.00,
        "source_url": "https://detail.1688.com/offer/660123456002.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-zhiwu-1.jpg",
        ],
        "shop_name": "佛山顺德厨卫五金厂",
        "sales": 89600,
        "location": "广东 佛山",
    },
    {
        "product_id": "1688_660123456003",
        "title": "食品级冰箱保鲜盒透明带盖 特大号储物盒批发一件代发",
        "price": 3.20,
        "source_url": "https://detail.1688.com/offer/660123456003.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-bingxiang-1.jpg",
            "https://cbu01.alicdn.com/img/ibank/2023/demo-bingxiang-2.jpg",
        ],
        "shop_name": "临沂塑料注塑加工厂",
        "sales": 568000,
        "location": "山东 临沂",
    },
    {
        "product_id": "1688_660123456004",
        "title": "可调节抽屉分隔收纳盒 厨房桌面杂物整理盒源头工厂",
        "price": 2.80,
        "source_url": "https://detail.1688.com/offer/660123456004.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-chouti-1.jpg",
        ],
        "shop_name": "台州黄岩收纳塑料制品厂",
        "sales": 245000,
        "location": "浙江 台州",
    },
    {
        "product_id": "1688_660123456005",
        "title": "免打孔不锈钢厨房挂架 刀架铲勺挂钩置物架厂家批发",
        "price": 8.50,
        "source_url": "https://detail.1688.com/offer/660123456005.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-guajia-1.jpg",
        ],
        "shop_name": "广州厨卫五金制品有限公司",
        "sales": 134000,
        "location": "广东 广州",
    },
    {
        "product_id": "1688_660123456006",
        "title": "玻璃储粮罐密封罐 厨房干货杂粮收纳罐带盖一件代发",
        "price": 4.20,
        "source_url": "https://detail.1688.com/offer/660123456006.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-mifengguan-1.jpg",
            "https://cbu01.alicdn.com/img/ibank/2023/demo-mifengguan-2.jpg",
        ],
        "shop_name": "徐州玻璃器皿源头工厂",
        "sales": 198000,
        "location": "江苏 徐州",
    },
    {
        "product_id": "1688_660123456007",
        "title": "不锈钢沥水架碗碟架 洗碗池旁置物架沥干架工厂直供",
        "price": 13.50,
        "source_url": "https://detail.1688.com/offer/660123456007.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-loushui-1.jpg",
        ],
        "shop_name": "揭阳不锈钢厨具批发商行",
        "sales": 76500,
        "location": "广东 揭阳",
    },
    {
        "product_id": "1688_660123456008",
        "title": "360度旋转收纳盘 厨房调料转盘化妆品收纳旋转托盘批发",
        "price": 4.80,
        "source_url": "https://detail.1688.com/offer/660123456008.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-zhuanpan-1.jpg",
        ],
        "shop_name": "义乌懒人日用品有限公司",
        "sales": 287000,
        "location": "浙江 义乌",
    },
    {
        "product_id": "1688_660123456009",
        "title": "可伸缩水槽下收纳架 厨房橱柜下水管旁置物架厂家直销",
        "price": 10.80,
        "source_url": "https://detail.1688.com/offer/660123456009.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-shuicao-1.jpg",
            "https://cbu01.alicdn.com/img/ibank/2023/demo-shuicao-2.jpg",
        ],
        "shop_name": "泉州空间收纳家具厂",
        "sales": 54200,
        "location": "福建 泉州",
    },
    {
        "product_id": "1688_660123456010",
        "title": "厨房挂式垃圾袋收纳盒 免打孔壁挂塑料袋整理架一件代发",
        "price": 2.20,
        "source_url": "https://detail.1688.com/offer/660123456010.html",
        "image_urls": [
            "https://cbu01.alicdn.com/img/ibank/2023/demo-lajidai-1.jpg",
        ],
        "shop_name": "义乌日用百货源头工厂",
        "sales": 890000,
        "location": "浙江 义乌",
    },
]


def _filter_demo_by_keyword(demo_data: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    """根据关键词过滤模拟数据（关键词命中标题即保留，无命中则返回全部）"""
    if not keyword:
        return demo_data
    kw = keyword.strip().lower()
    matched = [d for d in demo_data if kw in d["title"].lower()]
    return matched if matched else demo_data


# ========== 拼多多货源 ==========

class PinduoduoSource:
    """拼多多货源搜索（移动端接口）"""

    PLATFORM = "pinduoduo"
    SEARCH_URL = "https://mobile.yangkeduo.com/proxy/api/search/goods"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        # 拼多多移动端常见请求头
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://mobile.yangkeduo.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    async def search(self, keyword: str, limit: int = 20) -> list[SourceProduct]:
        """
        在拼多多搜索商品
        :param keyword: 搜索关键词
        :param limit: 返回数量上限
        :return: SourceProduct 列表
        """
        logger.info(f"[货源-拼多多] 搜索关键词: {keyword} (limit={limit})")
        try:
            params = {
                "keyword": keyword,
                "page": 1,
                "size": min(limit, 50),
                "sort": "price_asc",  # 按价格升序，优先低价货源
            }
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                # 反爬延时，模拟人类操作节奏
                await asyncio.sleep(random.uniform(0.8, 2.0))
                resp = await client.get(self.SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            goods_list = data.get("goods_list") or data.get("items") or []
            if not goods_list:
                logger.warning("[货源-拼多多] 接口返回空结果，降级为演示数据")
                return self._demo_search(keyword, limit)

            products: list[SourceProduct] = []
            for item in goods_list[:limit]:
                products.append(self._parse_item(item))
            logger.info(f"[货源-拼多多] 搜索到 {len(products)} 条真实结果")
            return products

        except httpx.HTTPStatusError as e:
            logger.warning(f"[货源-拼多多] 接口HTTP错误 {e.response.status_code}，降级为演示数据")
            return self._demo_search(keyword, limit)
        except (httpx.RequestError, ValueError, KeyError) as e:
            logger.warning(f"[货源-拼多多] 请求异常: {e}，降级为演示数据")
            return self._demo_search(keyword, limit)
        except Exception as e:
            logger.error(f"[货源-拼多多] 未预期异常: {e}，降级为演示数据")
            return self._demo_search(keyword, limit)

    def _parse_item(self, item: dict[str, Any]) -> SourceProduct:
        """解析拼多多接口返回的单条商品（字段名按实际接口维护）"""
        goods_id = str(item.get("goods_id") or item.get("id") or "")
        image_urls = []
        thumb = item.get("thumb_url") or item.get("image_url")
        if thumb:
            image_urls = [thumb] if isinstance(thumb, str) else list(thumb)
        return SourceProduct(
            product_id=goods_id,
            title=item.get("goods_name") or item.get("title") or "未知商品",
            price=float(item.get("normal_price", 0) or item.get("price", 0) or 0) / 100,  # 接口价格单位为分
            source_url=f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}",
            image_urls=image_urls,
            platform=self.PLATFORM,
            shop_name=item.get("mall_name") or "拼多多商家",
            sales=int(item.get("sales_tip") or item.get("cnt") or 0),
            location=item.get("ship_from") or "",
        )

    def _demo_search(self, keyword: str, limit: int) -> list[SourceProduct]:
        """演示模式：返回带 DEMO 标记的模拟数据"""
        logger.info(f"[货源-拼多多][DEMO] 返回模拟数据，关键词: {keyword}")
        matched = _filter_demo_by_keyword(_PINDDUODUO_DEMO, keyword)[:limit]
        products: list[SourceProduct] = []
        for item in matched:
            products.append(SourceProduct(
                product_id=item["product_id"],
                title=f"[DEMO]{item['title']}",
                price=item["price"],
                source_url=item["source_url"],
                image_urls=list(item["image_urls"]),
                platform=self.PLATFORM,
                shop_name=item["shop_name"],
                sales=item["sales"],
                location=item["location"],
            ))
        return products


# ========== 1688货源 ==========

class Alibaba1688Source:
    """1688货源搜索（H5 mtop 接口）"""

    PLATFORM = "alibaba1688"
    SEARCH_URL = "https://h5api.m.1688.com/h5/mtop.alibaba.search.app.searchresult/1.0/"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        # 1688 H5 端常见请求头
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://m.1688.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    async def search(self, keyword: str, limit: int = 20) -> list[SourceProduct]:
        """
        在1688搜索商品
        :param keyword: 搜索关键词
        :param limit: 返回数量上限
        :return: SourceProduct 列表
        """
        logger.info(f"[货源-1688] 搜索关键词: {keyword} (limit={limit})")
        try:
            params = {
                "keyword": keyword,
                "beginPage": 1,
                "pageSize": min(limit, 50),
                "sortType": "price_asc",  # 按价格升序，优先低价批发货源
            }
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                # 反爬延时，模拟人类操作节奏
                await asyncio.sleep(random.uniform(0.8, 2.0))
                resp = await client.get(self.SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            # mtop 接口标准返回结构 data.resultList
            result_list = (
                data.get("data", {}).get("resultList")
                or data.get("data", {}).get("offerList")
                or []
            )
            if not result_list:
                logger.warning("[货源-1688] 接口返回空结果，降级为演示数据")
                return self._demo_search(keyword, limit)

            products: list[SourceProduct] = []
            for item in result_list[:limit]:
                products.append(self._parse_item(item))
            logger.info(f"[货源-1688] 搜索到 {len(products)} 条真实结果")
            return products

        except httpx.HTTPStatusError as e:
            logger.warning(f"[货源-1688] 接口HTTP错误 {e.response.status_code}，降级为演示数据")
            return self._demo_search(keyword, limit)
        except (httpx.RequestError, ValueError, KeyError) as e:
            logger.warning(f"[货源-1688] 请求异常: {e}，降级为演示数据")
            return self._demo_search(keyword, limit)
        except Exception as e:
            logger.error(f"[货源-1688] 未预期异常: {e}，降级为演示数据")
            return self._demo_search(keyword, limit)

    def _parse_item(self, item: dict[str, Any]) -> SourceProduct:
        """解析1688接口返回的单条商品（字段名按实际接口维护）"""
        offer_id = str(item.get("offerId") or item.get("id") or "")
        image_urls = []
        image = item.get("image") or item.get("imgUrl")
        if image:
            if isinstance(image, str):
                image_urls = [image if image.startswith("http") else f"https:{image}"]
            else:
                image_urls = [i if str(i).startswith("http") else f"https:{i}" for i in image]
        return SourceProduct(
            product_id=offer_id,
            title=item.get("title") or item.get("subject") or "未知商品",
            price=float(item.get("priceInfo", {}).get("price", 0) or item.get("price", 0) or 0),
            source_url=f"https://detail.1688.com/offer/{offer_id}.html",
            image_urls=image_urls,
            platform=self.PLATFORM,
            shop_name=item.get("companyName") or item.get("shopName") or "1688商家",
            sales=int(item.get("tradeQuantity") or item.get("monthSold") or 0),
            location=item.get("city") or item.get("region") or "",
        )

    def _demo_search(self, keyword: str, limit: int) -> list[SourceProduct]:
        """演示模式：返回带 DEMO 标记的模拟数据"""
        logger.info(f"[货源-1688][DEMO] 返回模拟数据，关键词: {keyword}")
        matched = _filter_demo_by_keyword(_ALIBABA1688_DEMO, keyword)[:limit]
        products: list[SourceProduct] = []
        for item in matched:
            products.append(SourceProduct(
                product_id=item["product_id"],
                title=f"[DEMO]{item['title']}",
                price=item["price"],
                source_url=item["source_url"],
                image_urls=list(item["image_urls"]),
                platform=self.PLATFORM,
                shop_name=item["shop_name"],
                sales=item["sales"],
                location=item["location"],
            ))
        return products


# ========== 货源管理器 ==========

class SourceManager:
    """
    统一管理多个货源平台
    - 并发检索拼多多、1688，合并去重后按价格排序
    - 提供货源价 vs 闲鱼售价的利润空间分析
    """

    def __init__(self, pinduoduo: PinduoduoSource | None = None,
                 alibaba: Alibaba1688Source | None = None):
        self.pinduoduo = pinduoduo or PinduoduoSource()
        self.alibaba = alibaba or Alibaba1688Source()

    async def search_all(self, keyword: str, limit: int = 20) -> list[SourceProduct]:
        """
        并发搜索全部货源平台，合并去重并按价格升序排序
        :param keyword: 搜索关键词
        :param limit: 每个平台返回上限（合并后总数约 2*limit，再按价格取优）
        :return: 合并去重后的 SourceProduct 列表（价格升序）
        """
        logger.info(f"[货源管理器] 并发检索: {keyword}")
        # 并发请求两个平台
        pdd_task = self.pinduoduo.search(keyword, limit=limit)
        ali_task = self.alibaba.search(keyword, limit=limit)
        results: list[list[SourceProduct]] = await asyncio.gather(
            pdd_task, ali_task, return_exceptions=True
        )

        merged: list[SourceProduct] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning(f"[货源管理器] 某平台检索失败: {res}")
                continue
            merged.extend(res)

        # 去重：按标题归一化（去除 [DEMO] 前缀与空白）后保留价格最低者
        deduped = self._dedup(merged)
        # 按价格升序排序（低价货源优先）
        deduped.sort(key=lambda p: p.price)
        logger.info(
            f"[货源管理器] 合并后 {len(merged)} 条，去重后 {len(deduped)} 条，已按价格升序排序"
        )
        return deduped

    @staticmethod
    def _normalize_title(title: str) -> str:
        """标题归一化：去除 [DEMO] 标记、空格、常见营销词，便于去重比对"""
        import re
        t = title.replace("[DEMO]", "")
        # 去除常见的批发/营销后缀词，使跨平台相似商品可对齐
        marketing_words = ["批发", "一件代发", "厂家直销", "工厂直供", "源头工厂",
                           "厂家直供", "直供", "直销", "厂家", "工厂"]
        for w in marketing_words:
            t = t.replace(w, "")
        t = re.sub(r"\s+", "", t)
        return t.lower()

    def _dedup(self, products: list[SourceProduct]) -> list[SourceProduct]:
        """按归一化标题去重，保留价格最低的商品"""
        bucket: dict[str, SourceProduct] = {}
        for p in products:
            key = self._normalize_title(p.title)
            existing = bucket.get(key)
            if existing is None or p.price < existing.price:
                bucket[key] = p
        return list(bucket.values())

    @staticmethod
    def compare_price(source_price: float, xianyu_avg_price: float) -> dict[str, Any]:
        """
        计算货源价与闲鱼均价的价差及利润空间
        :param source_price: 货源采购单价（元）
        :param xianyu_avg_price: 闲鱼同款均价（元）
        :return: {
            price_diff: 价差（元）,
            profit_margin: 利润率（%）,
            profit_amount: 单件利润（元）,
            is_profitable: 是否有利可图,
            recommendation: 选品建议,
        }
        """
        # 预估成本：货源价 + 运费 + 闲鱼交易手续费（约5%）
        config = get_config()
        # 默认运费3元，无货源代发无额外运费；手续费取闲鱼担保交易约5%
        estimated_freight = 3.0
        commission_rate = 0.05
        commission = xianyu_avg_price * commission_rate
        total_cost = source_price + estimated_freight + commission

        profit_amount = xianyu_avg_price - total_cost
        price_diff = xianyu_avg_price - source_price
        profit_margin = (profit_amount / xianyu_avg_price * 100) if xianyu_avg_price > 0 else 0.0
        is_profitable = profit_amount > 0

        # 选品建议阈值
        if profit_margin >= 40:
            recommendation = "强烈推荐：利润空间充足，优先上架"
        elif profit_margin >= 20:
            recommendation = "推荐：利润尚可，可上架"
        elif profit_margin >= 0:
            recommendation = "谨慎：利润微薄，需控价或提升售价"
        else:
            recommendation = "放弃：亏损，不建议上架"

        return {
            "source_price": round(source_price, 2),
            "xianyu_avg_price": round(xianyu_avg_price, 2),
            "estimated_cost": round(total_cost, 2),
            "price_diff": round(price_diff, 2),
            "profit_amount": round(profit_amount, 2),
            "profit_margin": round(profit_margin, 2),
            "is_profitable": is_profitable,
            "recommendation": recommendation,
        }


# ========== 自测入口 ==========

async def _self_test():
    """直接运行本文件时的自测：搜索+比价"""
    manager = SourceManager()
    keyword = "厨房收纳盒"
    products = await manager.search_all(keyword, limit=10)
    logger.info(f"=== 搜索结果（{keyword}）共 {len(products)} 条 ===")
    for p in products:
        logger.info(
            f"[{p.platform}] ¥{p.price:.2f} | {p.title} | {p.shop_name} | {p.location}"
        )

    # 比价示例：货源价8.9，闲鱼均价19.9
    if products:
        cheapest = products[0]
        result = manager.compare_price(cheapest.price, 19.9)
        logger.info(f"=== 比价结果（货源¥{cheapest.price} vs 闲鱼均价¥19.9）===")
        for k, v in result.items():
            logger.info(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(_self_test())
