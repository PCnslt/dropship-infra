"""
Pricing engine for the dropshipping marketplace.

list_price = source_cost × markup + shipping + skim, reserve-aware.

DAP: we never quote or guarantee import duty/tax — the buyer pays those at their
border. We only price the product + shipping + our margin, and disclose honestly.
"""
from dataclasses import dataclass, field


MARKUP_RATE = 2.0          # source cost × 2 (≈$38 source → $76 list)
SHIPPING_SKIM_RATE = 0.06  # 6% on shipping
SHIPPING_SKIM_FLAT = 0.50  # CAD min
RESERVE_RATE = 0.10        # 10% of list price held for returns/chargebacks
PAYMENT_RATE = 0.03        # ~3% PSP fee


@dataclass
class DropshipPrice:
    source_cost: float
    source_currency: str
    shipping: float
    list_price: float
    platform_margin: float
    reserve: float
    payment_fee: float
    net_profit: float
    currency: str = "CAD"
    warnings: list = field(default_factory=list)


def price_product(source_cost_cad, shipping_cad):
    """Compute the list price + margin breakdown for one product."""
    markup = round(source_cost_cad * MARKUP_RATE, 2)
    skim = round(shipping_cad * SHIPPING_SKIM_RATE + SHIPPING_SKIM_FLAT, 2)
    list_price = round(markup + skim, 2)
    reserve = round(list_price * RESERVE_RATE, 2)
    payment_fee = round(list_price * PAYMENT_RATE, 2)
    platform_margin = round((markup - source_cost_cad) + skim, 2)
    net_profit = round(platform_margin - reserve - payment_fee, 2)
    return DropshipPrice(
        source_cost=source_cost_cad, source_currency="USD",
        shipping=shipping_cad, list_price=list_price,
        platform_margin=platform_margin, reserve=reserve,
        payment_fee=payment_fee, net_profit=net_profit,
    )
