#!/usr/bin/env python3
"""
ASAT Order Validator - FINAL Version with Correct Logic
Validation Hierarchy:
1. Order canceled? → DO NOT resend
2. Has error?
   - Is V041? → Check other orders for the article
   - Other error? → DO NOT resend
3. No error? → Validate revenue model
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Iterator, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# URL Configuration
ASAT_ORDER_URL = "https://wpp-admin-wprod.aws.wiley.com/services/wpp-admin-app/orderManagement/orders/{order_id}"
PRODUCT_DETAILS_URL = "https://wpp-admin-wprod.aws.wiley.com/services/wpp-admin-app/productDetails/v1/article/{article_id}"
MULTIPLE_ORDERS_URL = "https://wpp-admin-wprod.aws.wiley.com/services/wpp-admin-app/orderManagement/orders"
RESEND_URL = "http://as-order-svc-wprod.aws.wiley.com:8080/v1/orders/resend"
AUTH_URL = "https://wpp-admin-wprod.aws.wiley.com/services/wpp-admin-app/authenticate"

DEFAULT_TIMEOUT = 10.0
MAX_WORKERS = 10

# Logger will be configured in main
logger = logging.getLogger(__name__)


def setup_logging(output_dir: Path, verbose: bool = False) -> Path:
    """
    Configure logging for console and file

    Returns:
        Path to the created log file
    """
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"validation_log_{timestamp}.log"
    log_path = output_dir / log_filename

    # Configure level
    log_level = logging.DEBUG if verbose else logging.INFO

    # Clear existing handlers
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)  # Logger always in DEBUG

    # Detailed format
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)-8s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler (always DEBUG - everything goes to file)
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler (depends on --verbose)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return log_path


@dataclass
class OrderResult:
    """Result of order query and validation"""
    order_id: str
    article_id: Optional[str] = None
    order_status: Optional[str] = None

    # Payment details
    payment_method: Optional[str] = None
    total_charged: Optional[float] = None

    # Journal details
    journal_name: Optional[str] = None
    revenue_model: Optional[str] = None

    # Article details
    article_doi: Optional[str] = None

    # Error detection
    has_error: bool = False
    error_code: Optional[str] = None
    error_description: Optional[str] = None

    # V041 specific
    is_v041_error: bool = False
    other_orders: List[dict] = field(default_factory=list)
    other_orders_not_canceled: int = 0
    canceled_order_has_credit_memo: Optional[bool] = None

    # Validation result
    can_resend: bool = False
    validation_reason: str = ""
    validation_step: str = ""

    # Resend status
    resend_status: Optional[str] = None
    resend_error: Optional[str] = None

    # Context
    context: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        data = asdict(self)
        context = data.pop('context', {})
        # Remove None fields for cleaner output
        data = {k: v for k, v in data.items() if v is not None and v != [] and v != {}}
        return {**context, **data}


def create_session(auth_user: str, auth_pass: str, timeout: float = DEFAULT_TIMEOUT) -> requests.Session:
    """Create authenticated HTTP session"""
    session = requests.Session()

    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=20,
        pool_maxsize=20
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        logger.info(f"🔐 Authenticating as: {auth_user}")
        payload = {"username": auth_user, "password": auth_pass, "rememberMe": False}
        resp = session.post(AUTH_URL, json=payload, timeout=timeout)

        if resp.ok and session.cookies:
            logger.info("✅ Authentication successful")
        else:
            logger.error(f"❌ Authentication failed: HTTP {resp.status_code}")
    except requests.RequestException as e:
        logger.error(f"❌ Authentication error: {e}")

    return session


def get_order_details(order_id: str, session: requests.Session, timeout: float) -> Optional[dict]:
    """Query order details"""
    url = ASAT_ORDER_URL.format(order_id=order_id)

    try:
        resp = session.get(url, timeout=timeout)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.debug(f"Error querying order {order_id}: {e}")

    return None


def get_product_details(article_id: str, session: requests.Session, timeout: float) -> Optional[dict]:
    """Query product/article details to get revenue model"""
    url = PRODUCT_DETAILS_URL.format(article_id=article_id)

    try:
        resp = session.get(url, timeout=timeout)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.debug(f"Error querying product details for {article_id}: {e}")

    return None


def get_multiple_orders(article_id: str, session: requests.Session, timeout: float) -> List[dict]:
    """Query all orders for an article"""
    # Remove "PD" prefix if exists
    clean_article_id = article_id.replace("PD", "") if article_id else article_id

    # Use dhId query parameter
    url = f"{MULTIPLE_ORDERS_URL}?dhId={clean_article_id}"

    try:
        logger.debug(f"Querying multiple orders: {url}")
        resp = session.get(url, timeout=timeout)

        if resp.ok:
            data = resp.json()
            # Return list of orders from payload
            if isinstance(data, dict) and "payload" in data:
                orders = data["payload"]
                logger.debug(f"Found {len(orders)} order(s) for article {clean_article_id}")
                return orders
            else:
                logger.debug(
                    f"Response does not contain payload: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        else:
            logger.debug(f"HTTP error {resp.status_code} querying multiple orders")
    except Exception as e:
        logger.debug(f"Error querying multiple orders for {article_id}: {e}")

    return []


def check_error_in_history(order_history: list) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Check if there is an error in the order history

    Returns:
        (has_error, error_code, error_description)
    """
    if not order_history or not isinstance(order_history, list):
        return False, None, None

    for event in order_history:
        if not isinstance(event, dict):
            continue

        event_type = event.get("eventType", "")
        event_desc = event.get("eventDescription", "")

        # Detect error
        if "Error" in event_type or "error" in event_type.lower():
            # Extract error code (ex: V041)
            error_code = None
            if "V041" in event_desc:
                error_code = "V041"
            elif ":" in event_desc:
                # Tentar extrair código antes do ":"
                parts = event_desc.split(":")
                potential_code = parts[0].strip()
                if len(potential_code) < 10:  # Códigos são curtos
                    error_code = potential_code

            return True, error_code, event_desc

    return False, None, None


def check_credit_memo_in_history(order_history: list) -> bool:
    """Check if there is a credit memo in history"""
    if not order_history or not isinstance(order_history, list):
        return False

    for event in order_history:
        if not isinstance(event, dict):
            continue

        event_type = event.get("eventType", "")
        if "Credit memo created" in event_type:
            return True

    return False


def validate_revenue_model_rules(
        revenue_model: str,
        payment_method: str,
        total_charged: float
) -> tuple[bool, str]:
    """
    Validate revenue model rules

    Returns:
        (can_resend, reason)
    """
    # OO
    if revenue_model == "OO":
        if total_charged == 0:
            return False, "OO with totalChargedAmount = 0"
        else:
            return True, f"OO with totalChargedAmount > 0 (${total_charged})"

    # OA
    elif revenue_model == "OA":
        if payment_method == "Invoice":
            if total_charged == 0:
                return False, "OA + Invoice with totalChargedAmount = 0"
            else:
                return True, f"OA + Invoice with totalChargedAmount > 0 (${total_charged})"
        else:
            return True, f"OA + {payment_method} (regardless of totalChargedAmount)"

    # Other revenue models
    else:
        return True, f"Revenue Model {revenue_model}"


def validate_order(
        order_id: str,
        session: requests.Session,
        timeout: float,
        context: dict,
        debug: bool = False
) -> OrderResult:
    """
    Validate an order following the specified rule hierarchy
    """
    logger.debug(f"")
    logger.debug(f"{'=' * 80}")
    logger.debug(f"🔍 VALIDATING ORDER: {order_id}")
    logger.debug(f"{'=' * 80}")

    result = OrderResult(order_id=order_id, context=context)

    # PASSO 1: Obter detalhes da order
    logger.debug(f"STEP 1: Querying order details for {order_id}")
    order_data = get_order_details(order_id, session, timeout)

    if not order_data:
        result.error = "Could not query the order"
        result.validation_reason = "Error querying ASAT"
        result.validation_step = "1. Order query"
        logger.warning(f"❌ {order_id}: Failed to query ASAT")
        return result

    logger.debug(f"✅ Order {order_id} queried successfully")

    # Extrair informações básicas
    if "orderDetails" in order_data:
        order_details = order_data["orderDetails"]
        result.order_status = order_details.get("orderStatus")
        order_history = order_details.get("orderHistory", [])
        logger.debug(f"   Status: {result.order_status}")
    else:
        result.validation_reason = "orderDetails not found na resposta"
        result.validation_step = "1. Order query"
        logger.error(f"❌ {order_id}: orderDetails not found")
        return result

    if "article" in order_data:
        article = order_data["article"]
        result.article_id = article.get("id")
        result.article_doi = article.get("doi")
        logger.debug(f"   Article ID: {result.article_id}")
        logger.debug(f"   DOI: {result.article_doi}")

    if "journal" in order_data:
        result.journal_name = order_data["journal"].get("name")
        logger.debug(f"   Journal: {result.journal_name}")

    if "paymentDetails" in order_data:
        payment = order_data["paymentDetails"]
        result.payment_method = payment.get("paymentMethod")
        result.total_charged = payment.get("totalChargedAmount", 0)
        logger.debug(f"   Payment: {result.payment_method} (${result.total_charged})")

    # RULE 1: If order is canceled → DO NOT RESEND
    logger.debug(f"")
    logger.debug(f"STEP 2: Checking if order is canceled")
    if result.order_status == "OrderCanceledInAMP":
        result.can_resend = False
        result.validation_reason = "Order canceled (OrderCanceledInAMP)"
        result.validation_step = "1. Canceled status check"
        logger.info(f"⚠️  {order_id}: BLOCKED - Order canceled")
        return result

    logger.debug(f"✅ Order is not canceled")

    # REGRA 2: Check for errors
    logger.debug(f"")
    logger.debug(f"STEP 3: Checking for errors in history")
    has_error, error_code, error_desc = check_error_in_history(order_history)
    result.has_error = has_error
    result.error_code = error_code
    result.error_description = error_desc

    if has_error:
        logger.debug(f"⚠️  Error detected: {error_code} - {error_desc}")

        # REGRA 2.1: Is V041 error?
        if error_code == "V041":
            logger.info(f"🔍 {order_id}: V041 error detected - verifying other orders")
            result.is_v041_error = True
            result.validation_step = "2. V041 error detection"

            if not result.article_id:
                result.validation_reason = "Erro V041 detected but article_id not available"
                result.can_resend = False
                logger.warning(f"❌ {order_id}: V041 without article_id")
                return result

            # Check other orders for the article
            logger.debug(f"   Querying other orders for article {result.article_id}")
            other_orders = get_multiple_orders(result.article_id, session, timeout)
            result.other_orders = other_orders

            if not other_orders:
                result.validation_reason = "V041 error: could not verify other orders for the article"
                result.can_resend = False
                logger.error(f"❌ {order_id}: Could not query other orders")
                return result

            if debug:
                logger.debug(f"V041 - Artigo {result.article_id}: {len(other_orders)} order(s) encontrada(s)")
                for o in other_orders:
                    logger.debug(f"  Order {o.get('orderUniqueId')}: {o.get('orderStatus')}")

            # Count non-canceled orders (excluding current)
            not_canceled = [
                o for o in other_orders
                if str(o.get("orderUniqueId")) != str(order_id)
                   and (o.get("orderStatus") != "OrderCanceledInAMP" or not o.get("inCancelledState", False))
            ]
            result.other_orders_not_canceled = len(not_canceled)

            if debug and not_canceled:
                logger.debug(f"V041 - Non-canceled orders: {[o.get('orderUniqueId') for o in not_canceled]}")

            # RULE 2.1.1: If there is more than one non-canceled order → REVIEW VIAX
            if len(not_canceled) > 0:
                result.can_resend = False
                result.validation_reason = f"V041 error: {len(not_canceled)} non-canceled order(s) detected - requires VIAX review"
                result.validation_step = "2.1. V041 - Multiple active orders"
                logger.warning(f"⚠️  {order_id}: V041 - {len(not_canceled)} order(s) ativa(s) detectada(s)")
                return result

            # Encontrar orders canceladas
            canceled_orders = [
                o for o in other_orders
                if str(o.get("orderUniqueId")) != str(order_id)
                   and o.get("orderStatus") == "OrderCanceledInAMP"
            ]

            if debug:
                logger.debug(f"V041 - Canceled orders: {[o.get('orderUniqueId') for o in canceled_orders]}")

            if not canceled_orders:
                result.can_resend = False
                result.validation_reason = "V041 error: no canceled order found"
                result.validation_step = "2.1. V041 - No canceled orders"
                logger.warning(f"⚠️  {order_id}: V041 - No canceled order")
                return result

            # Check credit memo in canceled order(s)
            logger.debug(f"   Checking credit memo in canceled orders")
            has_credit_memo = False
            for canceled in canceled_orders:
                canceled_id = canceled.get("orderUniqueId")
                if canceled_id:
                    if debug:
                        logger.debug(f"V041 - Checking credit memo in order {canceled_id}")

                    canceled_data = get_order_details(str(canceled_id), session, timeout)
                    if canceled_data and "orderDetails" in canceled_data:
                        canceled_history = canceled_data["orderDetails"].get("orderHistory", [])
                        if check_credit_memo_in_history(canceled_history):
                            has_credit_memo = True
                            if debug:
                                logger.debug(f"V041 - Credit memo found in order {canceled_id}")
                            logger.info(f"✅ {order_id}: V041 - Credit memo found in order {canceled_id}")
                            break
                        elif debug:
                            logger.debug(f"V041 - Credit memo NOT found in order {canceled_id}")

            result.canceled_order_has_credit_memo = has_credit_memo

            # RULE 2.1.2: If there is no credit memo → REVIEW VIAX
            if not has_credit_memo:
                result.can_resend = False
                result.validation_reason = "V041 error: canceled order without credit memo - requires VIAX review"
                result.validation_step = "2.1. V041 - No credit memo"
                logger.warning(f"⚠️  {order_id}: V041 - Canceled order without credit memo")
                return result

            # Se chegou aqui: V041 resolvido, continuar para validação de revenue model
            result.validation_step = "2.1. V041 - Ignored, validating revenue model"
            logger.info(f"✅ {order_id}: V041 ignored - continuing validation")

        else:
            # RULE 2.2: Other type of error → DO NOT RESEND
            result.can_resend = False
            result.validation_reason = f"Error detected: {error_code or 'DESCONHECIDO'} - {error_desc}"
            result.validation_step = "2.2. Other error detected"
            logger.warning(f"⚠️  {order_id}: BLOCKED - Error {error_code}")
            return result
    else:
        # No errors, continue to revenue model validation
        result.validation_step = "3. No errors, validating revenue model"
        logger.debug(f"✅ No errors detected in history")

    # RULE 3: Validate Revenue Model
    logger.debug(f"")
    logger.debug(f"STEP 4: Validating Revenue Model")

    if not result.article_id:
        result.can_resend = False
        result.validation_reason = "article_id not available para validação de revenue model"
        logger.error(f"❌ {order_id}: article_id not available")
        return result

    # Obter revenue model do productDetails
    logger.debug(f"   Querying productDetails for article {result.article_id}")
    product_data = get_product_details(result.article_id, session, timeout)

    if not product_data:
        result.can_resend = False
        result.validation_reason = "Could not query productDetails to get revenue model"
        logger.error(f"❌ {order_id}: Failed to query productDetails")
        return result

    # Extrair revenue model do journal
    if "journal" in product_data and isinstance(product_data["journal"], dict):
        result.revenue_model = product_data["journal"].get("revenueModel")
        logger.debug(f"   Revenue Model: {result.revenue_model}")

    if not result.revenue_model:
        result.can_resend = False
        result.validation_reason = "Revenue model not found em productDetails"
        logger.error(f"❌ {order_id}: Revenue model not found")
        return result

    # Apply revenue model rules
    logger.debug(f"   Applying Revenue Model rules...")
    can_resend, reason = validate_revenue_model_rules(
        result.revenue_model,
        result.payment_method or "",
        result.total_charged or 0
    )

    result.can_resend = can_resend

    if can_resend:
        if result.is_v041_error:
            result.validation_reason = f"✅ CAN RESEND: V041 ignored + {reason}"
            logger.info(f"✅ {order_id}: APPROVED - V041 ignored + {reason}")
        else:
            result.validation_reason = f"✅ CAN RESEND: {reason}"
            logger.info(f"✅ {order_id}: APPROVED - {reason}")
    else:
        result.validation_reason = f"❌ BLOCKED: {reason}"
        logger.warning(f"⚠️  {order_id}: BLOCKED - {reason}")

    return result


def process_file_parallel(
        file_path: Path,
        session: requests.Session,
        timeout: float,
        max_workers: int,
        debug: bool
) -> list[OrderResult]:
    """Process CSV file with parallel requests"""
    logger.info(f"📂 Processing: {file_path.name}")

    try:
        orders = list(read_csv_orders(file_path))
    except ValueError as e:
        logger.error(f"Error: {e}")
        return []

    if not orders:
        return []

    logger.info(f"🔄 Validating {len(orders)} orders ({max_workers} threads)...")

    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_order = {
            executor.submit(validate_order, order_id, session, timeout, context, debug): (order_id, context)
            for order_id, context in orders
        }

        for future in as_completed(future_to_order):
            try:
                result = future.result()
                results.append(result)

                if result.error:
                    logger.error(f"❌ {result.order_id}: {result.error}")
                elif result.can_resend:
                    logger.info(f"✅ {result.order_id}: {result.validation_reason}")
                else:
                    logger.warning(f"⚠️ {result.order_id}: {result.validation_reason}")

            except Exception as e:
                order_id, _ = future_to_order[future]
                logger.error(f"💥 {order_id}: {e}")

    return results


def read_csv_orders(file_path: Path) -> Iterator[tuple[str, dict]]:
    """Read CSV file"""
    with file_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)

        try:
            sniffer = csv.Sniffer()
            delimiter = sniffer.sniff(sample).delimiter
        except csv.Error:
            delimiter = "\t" if "\t" in sample else ","

        reader = csv.DictReader(fh, delimiter=delimiter)

        if not reader.fieldnames or "ORDER_UNIQUE_ID" not in reader.fieldnames:
            raise ValueError("Field ORDER_UNIQUE_ID missing")

        for row_num, row in enumerate(reader, start=2):
            order_id = (row.get("ORDER_UNIQUE_ID") or "").strip()
            if not order_id:
                continue

            context = {"file": file_path.name, "row_number": row_num}
            for field in ("ARTICLE_PRODUCT_ID", "WORKFLOW_STATUS"):
                if field in reader.fieldnames and row.get(field):
                    context[field] = row[field]

            yield order_id, context


def display_resendable_orders(results: list[OrderResult]) -> list[OrderResult]:
    """Display approved and blocked orders"""
    approved = [r for r in results if r.can_resend]
    blocked = [r for r in results if not r.can_resend and not r.error]

    if approved:
        print("\n" + "=" * 80)
        print("✅ ORDERS APPROVED FOR RESEND")
        print("=" * 80)

        for idx, r in enumerate(approved, start=1):
            print(f"\n{idx}. Order ID: {r.order_id}")
            print(f"   Status: {r.order_status}")
            print(f"   Reason: {r.validation_reason}")
            if r.revenue_model:
                print(f"   Revenue Model: {r.revenue_model}")
            if r.payment_method:
                print(f"   Payment: {r.payment_method} (${r.total_charged})")
            if r.is_v041_error:
                print(f"   ⚠️ V041 ignored (credit memo exist on canceled order)")

        print(f"\n{'=' * 80}")
        print(f"Total approved: {len(approved)}")
        print("=" * 80)

    if blocked:
        print("\n" + "=" * 80)
        print("🚫 BLOCKED ORDERS")
        print("=" * 80)

        for r in blocked:
            print(f"\n  • {r.order_id}")
            print(f"    Reason: {r.validation_reason}")
            print(f"    Step: {r.validation_step}")
            if r.revenue_model:
                print(f"    Revenue Model: {r.revenue_model}")

        print(f"\n{'=' * 80}")

    if not approved:
        logger.info("\nℹ️ No orders approved for resend")

    return approved


def ask_user_resend(approved: list[OrderResult]) -> List[OrderResult]:
    """Interactive menu"""
    if not approved:
        return []

    print("\n" + "=" * 80)
    print("OPTIONS")
    print("=" * 80)
    print("\n1. Resend ALL approved orders")
    print("2. Resend SPECIFIC orders")
    print("3. DO NOT resend")

    while True:
        try:
            choice = input("\nEnter the option (1/2/3): ").strip()

            if choice == "1":
                confirm = input(f"\n⚠️ Confirm resend of {len(approved)} order(s)? (Y/N): ").upper()
                return approved if confirm in ["Y", "YES", "N", "NO"] else []

            elif choice == "2":
                print(f"\nEnter the index number split by comma(ex: 1,3,5):")
                numbers = input("Index numbers: ").strip()

                try:
                    indices = [int(n.strip()) for n in numbers.split(",")]
                    selected = [approved[i - 1] for i in indices if 1 <= i <= len(approved)]

                    if selected:
                        print(f"\n📋 Selected: {', '.join(r.order_id for r in selected)}")
                        confirm = input(f"\n⚠️ Confirm? (Y/N): ").upper()
                        return selected if confirm in ["Y", "YES", "N", "NO"] else []
                except:
                    print("❌ Invalid format")
                    continue

            elif choice == "3":
                print("\n✅ Aborted.")
                return []

        except (KeyboardInterrupt, EOFError):
            print("\n❌ Aborted")
            return []


def resend_orders_batch(orders: List[OrderResult], session: requests.Session, timeout: float):
    """Resend orders"""
    if not orders:
        return

    print("\n" + "=" * 80)
    print("📤 RESEND")
    print("=" * 80)
    logger.info("")
    logger.info("=" * 80)
    logger.info(f"📤 STARTING RESEND OF {len(orders)} ORDER(S)")
    logger.info("=" * 80)

    for idx, order in enumerate(orders, start=1):
        print(f"\n[{idx}/{len(orders)}] {order.order_id}")
        logger.info(f"")
        logger.info(f"[{idx}/{len(orders)}] Resending order {order.order_id}")

        url = f"{RESEND_URL}?orderIds={order.order_id}"
        logger.debug(f"   URL: {url}")

        try:
            resp = session.post(url, timeout=timeout)

            if resp.ok:
                order.resend_status = "success"
                print(f"   ✅ Success")
                logger.info(f"   ✅ Resend successful")
                try:
                    data = resp.json()
                    if "message" in data:
                        logger.debug(f"   Message: {data['message']}")
                except:
                    pass
            else:
                order.resend_status = "failed"
                error_msg = f"HTTP {resp.status_code}"
                try:
                    data = resp.json()
                    if "message" in data or "error" in data:
                        error_msg = data.get("message", data.get("error", error_msg))
                except:
                    pass
                order.resend_error = error_msg
                print(f"   ❌ Failed: {error_msg}")
                logger.error(f"   ❌ Resend failed: {error_msg}")
        except Exception as e:
            order.resend_status = "failed"
            order.resend_error = str(e)
            print(f"   ❌ Error: {e}")
            logger.error(f"   ❌ Error resending: {e}")

    success = sum(1 for o in orders if o.resend_status == "success")
    failed = sum(1 for o in orders if o.resend_status == "failed")

    print(f"\n{'=' * 80}")
    print(f"✅ Successes: {success}/{len(orders)}")
    if failed > 0:
        print(f"❌ Failures: {failed}/{len(orders)}")
    print("=" * 80)

    logger.info("")
    logger.info("=" * 80)
    logger.info("📊 RESEND SUMMARY")
    logger.info("=" * 80)
    logger.info(f"✅ Successes: {success}/{len(orders)}")
    if failed > 0:
        logger.info(f"❌ Failures: {failed}/{len(orders)}")
        logger.info("")
        logger.info("Orders with failures:")
        for o in orders:
            if o.resend_status == "failed":
                logger.info(f"  • {o.order_id}: {o.resend_error}")
    logger.info("=" * 80)


def save_results(results: list[OrderResult], output_path: Path):
    """Save results"""
    with output_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    logger.info(f"💾 Saved: {output_path}")


def find_csv_files(input_dir: Path) -> list[Path]:
    """Find CSV files"""
    if not input_dir.exists() or not input_dir.is_dir():
        return []
    return sorted([p for p in input_dir.iterdir() if p.suffix.lower() == ".csv"])


def main():
    parser = argparse.ArgumentParser(description="Validator ASAT")

    default_input = Path(__file__).resolve().parent / "input"
    default_output = Path(__file__).resolve().parent / "output"

    parser.add_argument("--input-dir", "-i", type=Path, default=default_input)
    parser.add_argument("--output-dir", type=Path, default=default_output, help="Directory for logs and outputs")
    parser.add_argument("--timeout", "-t", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--output", "-o", type=Path, help="Output File JSONL (Will be saved at output-dir)")
    parser.add_argument("--max-workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--auth-user", default=os.getenv("WPP_AUTH_USER"))
    parser.add_argument("--auth-pass", default=os.getenv("WPP_AUTH_PASS"))
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-interactive", action="store_true")

    args = parser.parse_args()

    # Configure logging BEFORE any log
    log_path = setup_logging(args.output_dir, args.verbose)

    logger.info("=" * 80)
    logger.info("🚀 ASAT ORDER VALIDATOR - STARTING")
    logger.info("=" * 80)
    logger.info(f"📅 Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📝 Log saved to: {log_path}")
    logger.info(f"📂 Input: {args.input_dir}")
    logger.info(f"📂 Output: {args.output_dir}")
    logger.info(f"👤 User: {args.auth_user}")
    logger.info(f"🔧 Workers: {args.max_workers}")
    logger.info(f"⏱️  Timeout: {args.timeout}s")
    logger.info("=" * 80)

    if not args.auth_user or not args.auth_pass:
        logger.error("❌ Credentials required")
        return 1

    csv_files = find_csv_files(args.input_dir)
    if not csv_files:
        logger.error("❌ No CSV files found")
        return 1

    logger.info(f"📋 CSV files found: {len(csv_files)}")
    for csv_file in csv_files:
        logger.info(f"  • {csv_file.name}")
    logger.info("")

    session = create_session(args.auth_user, args.auth_pass, args.timeout)

    try:
        all_results = []

        for csv_file in csv_files:
            results = process_file_parallel(
                csv_file, session, args.timeout, args.max_workers, args.verbose
            )
            all_results.extend(results)

        approved = sum(1 for r in all_results if r.can_resend)
        blocked = sum(1 for r in all_results if not r.can_resend and not r.error)
        errors = sum(1 for r in all_results if r.error)

        logger.info("=" * 80)
        logger.info("📊 VALIDATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total orders: {len(all_results)}")
        logger.info(f"✅ Approved for resend: {approved}")
        logger.info(f"⚠️  Blocked by rules: {blocked}")
        if errors:
            logger.info(f"❌ Query errors: {errors}")
        logger.info("=" * 80)

        # Detalhamento dos bloqueios
        if blocked > 0:
            logger.info("\n📋 BLOCKING REASONS:")
            bloqueio_counts = {}
            for r in all_results:
                if not r.can_resend and not r.error:
                    reason = r.validation_reason
                    bloqueio_counts[reason] = bloqueio_counts.get(reason, 0) + 1

            for reason, count in sorted(bloqueio_counts.items(), key=lambda x: -x[1]):
                logger.info(f"  • {count}x: {reason}")
            logger.info("")

        resendable = display_resendable_orders(all_results)

        if resendable and not args.no_interactive:
            logger.info("=" * 80)
            logger.info("🔄 INTERACTIVE RESEND MENU")
            logger.info("=" * 80)
            to_resend = ask_user_resend(resendable)
            if to_resend:
                resend_orders_batch(to_resend, session, args.timeout)

        # Save output
        if args.output:
            output_path = args.output_dir / args.output.name
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = args.output_dir / f"validation_results_{timestamp}.jsonl"

        save_results(all_results, output_path)

        logger.info("=" * 80)
        logger.info("✅ PROCESSING COMPLETED")
        logger.info("=" * 80)
        logger.info(f"📝 Complete log: {log_path}")
        logger.info(f"💾 JSONL results: {output_path}")
        logger.info("=" * 80)

        return 0

    except KeyboardInterrupt:
        logger.info("\n❌ Processing interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"\n💥 Unexpected error: {e}", exc_info=True)
        return 1
    finally:
        session.close()
        logger.info("🔒 HTTP session closed")


if __name__ == "__main__":
    exit(main())