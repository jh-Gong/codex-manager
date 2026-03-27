"""
支付相关 API 路由
"""

import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...database.session import get_db
from ...database.models import Account
from ...database import crud
from ...config.settings import get_settings
from .accounts import resolve_account_ids
from ...core.openai.payment import (
    generate_plus_link,
    generate_team_link,
    open_url_incognito,
    check_subscription_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Pydantic Models ==============

class GenerateLinkRequest(BaseModel):
    account_id: int
    plan_type: str  # 'plus' or 'team'
    workspace_name: str = "MyTeam"
    price_interval: str = "month"
    seat_quantity: int = 5
    proxy: Optional[str] = None
    auto_open: bool = False  # 生成后是否自动无痕打开
    country: str = "SG"  # 计费国家，决定货币
    currency: Optional[str] = None  # 前端动态获取的货币代码，优先于静态映射表


class OpenIncognitoRequest(BaseModel):
    url: str
    account_id: Optional[int] = None  # 可选，用于注入账号 cookie


class MarkSubscriptionRequest(BaseModel):
    subscription_type: str  # 'free' / 'plus' / 'team'


class BatchCheckSubscriptionRequest(BaseModel):
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None


# ============== 支付链接生成 ==============

@router.post("/generate-link")
def generate_payment_link(request: GenerateLinkRequest):
    """生成 Plus 或 Team 支付链接，可选自动无痕打开"""
    with get_db() as db:
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        proxy = request.proxy or get_settings().get_proxy_url(db=db)

        try:
            if request.plan_type == "plus":
                link = generate_plus_link(account, proxy, country=request.country, currency=request.currency)
            elif request.plan_type == "team":
                link = generate_team_link(
                    account,
                    workspace_name=request.workspace_name,
                    price_interval=request.price_interval,
                    seat_quantity=request.seat_quantity,
                    proxy=proxy,
                    country=request.country,
                    currency=request.currency,
                )
            else:
                raise HTTPException(status_code=400, detail="plan_type 必须为 plus 或 team")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"生成支付链接失败: {e}")
            raise HTTPException(status_code=500, detail=f"生成链接失败: {str(e)}")

    opened = False
    if request.auto_open and link:
        cookies_str = account.cookies if account else None
        opened = open_url_incognito(link, cookies_str)

    return {
        "success": True,
        "link": link,
        "plan_type": request.plan_type,
        "auto_opened": opened,
    }


@router.post("/open-incognito")
def open_browser_incognito(request: OpenIncognitoRequest):
    """后端以无痕模式打开指定 URL，可注入账号 cookie"""
    if not request.url:
        raise HTTPException(status_code=400, detail="URL 不能为空")

    cookies_str = None
    if request.account_id:
        with get_db() as db:
            account = db.query(Account).filter(Account.id == request.account_id).first()
            if account:
                cookies_str = account.cookies

    success = open_url_incognito(request.url, cookies_str)
    if success:
        return {"success": True, "message": "已在无痕模式打开浏览器"}
    return {"success": False, "message": "未找到可用的浏览器，请手动复制链接"}


# ============== 订阅状态 ==============

@router.post("/accounts/batch-check-subscription")
def batch_check_subscription(request: BatchCheckSubscriptionRequest):
    """批量检测账号订阅状态"""
    results = {"success_count": 0, "failed_count": 0, "details": []}

    with get_db() as db:
        proxy = request.proxy or get_settings().get_proxy_url(db=db)
        ids = resolve_account_ids(
            db, request.ids, request.select_all,
            request.status_filter, request.email_service_filter, request.search_filter
        )
        for account_id in ids:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                results["failed_count"] += 1
                results["details"].append(
                    {"id": account_id, "email": None, "success": False, "error": "账号不存在"}
                )
                continue

            try:
                status = check_subscription_status(account, proxy)
                account.subscription_type = None if status == "free" else status
                account.subscription_at = datetime.utcnow() if status != "free" else account.subscription_at
                db.commit()
                results["success_count"] += 1
                results["details"].append(
                    {"id": account_id, "email": account.email, "success": True, "subscription_type": status}
                )
            except Exception as e:
                results["failed_count"] += 1
                results["details"].append(
                    {"id": account_id, "email": account.email, "success": False, "error": str(e)}
                )

    return results


@router.post("/accounts/{account_id}/mark-subscription")
def mark_subscription(account_id: int, request: MarkSubscriptionRequest):
    """手动标记账号订阅类型"""
    allowed = ("free", "plus", "team")
    if request.subscription_type not in allowed:
        raise HTTPException(status_code=400, detail=f"subscription_type 必须为 {allowed}")

    with get_db() as db:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        account.subscription_type = None if request.subscription_type == "free" else request.subscription_type
        account.subscription_at = datetime.utcnow() if request.subscription_type != "free" else None
        db.commit()

    return {"success": True, "subscription_type": request.subscription_type}

# ============== 国家/货币配置 ==============

_countries_cache: dict = {}  # {"data": [...], "expires_at": float}


def _get_fallback_countries():
    """内置 fallback 国家/货币列表"""
    return [
        {"country_code": "AU", "currency": "AUD", "country_name": "AU"},
        {"country_code": "BR", "currency": "BRL", "country_name": "BR"},
        {"country_code": "CA", "currency": "CAD", "country_name": "CA"},
        {"country_code": "GB", "currency": "GBP", "country_name": "GB"},
        {"country_code": "HK", "currency": "HKD", "country_name": "HK"},
        {"country_code": "IN", "currency": "INR", "country_name": "IN"},
        {"country_code": "JP", "currency": "JPY", "country_name": "JP"},
        {"country_code": "MX", "currency": "MXN", "country_name": "MX"},
        {"country_code": "SG", "currency": "SGD", "country_name": "SG"},
        {"country_code": "TR", "currency": "TRY", "country_name": "TR"},
        {"country_code": "US", "currency": "USD", "country_name": "US"},
    ]


@router.get("/countries")
def get_checkout_countries():
    """从 ChatGPT checkout 接口获取支持的国家/货币列表（优先读 DB 缓存，成功后回写）"""
    import time
    import json
    import curl_cffi.requests as cffi_requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _DB_CACHE_KEY = "cache.checkout_countries"
    now = time.time()

    # 1. 内存缓存命中
    if _countries_cache.get("expires_at", 0) > now:
        return {"success": True, "countries": _countries_cache["data"]}

    # 2. 读取 DB 缓存
    with get_db() as db:
        proxy = get_settings().get_proxy_url(db=db)
        db_setting = crud.get_setting(db, _DB_CACHE_KEY)
        if db_setting and db_setting.value:
            try:
                cached = json.loads(db_setting.value)
                if cached.get("expires_at", 0) > now:
                    _countries_cache.update(cached)
                    return {"success": True, "countries": cached["data"]}
            except Exception:
                pass

    proxies = {"http": proxy, "https": proxy} if proxy else None

    # 3. 请求 ChatGPT API 获取国家代码列表
    try:
        resp = cffi_requests.get(
            "https://chatgpt.com/backend-api/checkout_pricing_config/countries",
            proxies=proxies,
            timeout=15,
            impersonate="chrome110",
        )
        resp.raise_for_status()
        raw = resp.json()
        country_codes = raw.get("countries", []) if isinstance(raw, dict) else raw
        if not isinstance(country_codes, list) or not country_codes:
            raise ValueError(f"国家列表为空或格式异常: {str(raw)[:200]}")
    except Exception as e:
        logger.warning(f"获取国家代码列表失败: {e}")
        return {"success": False, "countries": _get_fallback_countries(), "error": str(e)}

    # 4. 并发请求各国 configs，提取 symbol_code
    def fetch_config(code: str):
        try:
            r = cffi_requests.get(
                f"https://chatgpt.com/backend-api/checkout_pricing_config/configs/{code}",
                proxies=proxies,
                timeout=10,
                impersonate="chrome110",
            )
            if r.status_code == 200:
                data = r.json()
                cfg = data.get("currency_config", {})
                currency = cfg.get("symbol_code") or cfg.get("symbol") or ""
                if currency:
                    return {"country_code": code, "currency": currency, "country_name": code}
        except Exception:
            pass
        return None

    countries = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_config, code): code for code in country_codes}
        for future in as_completed(futures):
            result = future.result()
            if result:
                countries.append(result)

    countries.sort(key=lambda c: c["country_code"])

    if not countries:
        logger.warning("所有国家 configs 请求均失败，使用 fallback")
        return {"success": False, "countries": _get_fallback_countries(), "error": "所有 configs 请求失败"}

    # 5. 写入内存缓存 + DB 缓存（缓存 7 天）
    expires_at = now + 86400 * 7
    cache_payload = {"data": countries, "expires_at": expires_at}
    _countries_cache.update(cache_payload)

    try:
        with get_db() as db:
            crud.set_setting(
                db,
                key=_DB_CACHE_KEY,
                value=json.dumps(cache_payload, ensure_ascii=False),
                description="checkout 国家/货币列表缓存",
                category="cache",
            )
    except Exception as e:
        logger.warning(f"写入 DB 缓存失败（不影响返回结果）: {e}")

    return {"success": True, "countries": countries}


